from __future__ import annotations
from typing import Optional

from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from functools import wraps

from flask import Flask, render_template, request, redirect, url_for, flash
from flask_login import LoginManager, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import select, func, text
from sqlalchemy.orm import Session, joinedload, selectinload

from db import engine, SessionLocal
from models import (
    Base,
    Account,
    ZoneType, ZoneStatus, Zone,
    Position, Employee,
    BookingStatus,
    Service,
    Client,
    ClientStatus,
    Booking,
    BookingService,
    Visit,
    Payment,
    ScheduleSlot,
    SubscriptionStatus,
    Subscription,
    Notification,
)

app = Flask(__name__)
app.secret_key = "dev-secret-change-me"

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"

app = Flask(__name__)
app.secret_key = "dev-secret-change-me"

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"

# --- FORCE LOGIN FOR ALL PAGES (кроме /login и статики) ---
from flask import request

PUBLIC_ENDPOINTS = {"login", "login_post", "client_register", "static"}

@app.before_request
def force_auth():
    if request.endpoint is None:
        return
    if request.endpoint in PUBLIC_ENDPOINTS:
        return
    if not current_user.is_authenticated:
        return redirect(url_for("login"))



def db_session() -> Session:
    return SessionLocal()


def money(x) -> Decimal:
    d = x if isinstance(x, Decimal) else Decimal(str(x))
    return d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def recalc_booking_total(s: Session, booking_id: int) -> None:
    """Пересчитать total_sum = session_sum + сумма услуг."""
    b = s.get(Booking, booking_id)
    if not b:
        return
    services_sum = (
        s.execute(select(func.coalesce(func.sum(BookingService.line_sum), 0)).where(BookingService.booking_id == booking_id))
        .scalar_one()
    )
    b.total_sum = money(Decimal(str(b.session_sum or 0)) + Decimal(str(services_sum or 0)))



def admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated:
            return login_manager.unauthorized()
        if getattr(current_user, "role", "") not in ("admin", "staff"):
            flash("Недостаточно прав", "danger")
            return redirect(url_for("dashboard"))
        return fn(*args, **kwargs)
    return wrapper


def client_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated:
            return login_manager.unauthorized()
        if getattr(current_user, "role", "") != "client":
            flash("Этот раздел доступен только клиентам", "warning")
            return redirect(url_for("dashboard"))
        return fn(*args, **kwargs)
    return wrapper


def parse_dt_local(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M")


@login_manager.user_loader
def load_user(user_id: str):
    with db_session() as s:
        return s.get(Account, int(user_id))


def seed_if_empty():
    """Создаёт таблицы и заполняет минимальные справочники/демо-данные.

    Важно: функция *обязательно* делает commit, чтобы учётка admin/admin создавалась.
    """
    Base.metadata.create_all(engine)
    with db_session() as s:
        def ensure_column(table: str, column: str, ddl: str) -> None:
            cols = [row[1] for row in s.execute(text(f"PRAGMA table_info({table})")).fetchall()]
            if column not in cols:
                s.execute(text(f"ALTER TABLE {table} ADD COLUMN {ddl}"))

        ensure_column("client", "status_id", "status_id INTEGER")
        ensure_column("booking", "schedule_slot_id", "schedule_slot_id INTEGER")
        ensure_column("booking", "subscription_id", "subscription_id INTEGER")

        # учётка администратора по умолчанию
        if not s.execute(select(Account).where(Account.login == "admin")).scalar_one_or_none():
            s.add(Account(login="admin", password_hash=generate_password_hash("admin"), role="admin"))

        # справочники со стабильными code
        def ensure(model, code: str, name: str):
            obj = s.execute(select(model).where(model.code == code)).scalar_one_or_none()
            if not obj:
                obj = model(code=code, name=name)
                s.add(obj)
            return obj

        ensure(ZoneStatus, "available", "Доступна")
        ensure(ZoneStatus, "maintenance", "На обслуживании")
        ensure(ZoneType, "trampoline", "Батутная арена")
        ensure(ZoneType, "foam_pit", "Поролоновая яма")
        ensure(BookingStatus, "new", "Новая")
        ensure(BookingStatus, "confirmed", "Подтверждена")
        ensure(BookingStatus, "cancelled", "Отменена")
        ensure(BookingStatus, "done", "Завершена")
        ensure(ClientStatus, "active", "Активен")
        ensure(ClientStatus, "blocked", "Заблокирован")
        ensure(SubscriptionStatus, "active", "Активен")
        ensure(SubscriptionStatus, "expired", "Истёк")
        ensure(SubscriptionStatus, "paused", "Приостановлен")

        # протолкнуть вставки справочников до выборок/связей
        s.flush()

        # демо-клиент
        if s.execute(select(func.count(Client.id))).scalar_one() == 0:
            status = s.execute(select(ClientStatus).where(ClientStatus.code == "active")).scalar_one()
            s.add(Client(full_name="Иванов Иван", phone="+7 900 000-00-00", status_id=status.id))
        else:
            status = s.execute(select(ClientStatus).where(ClientStatus.code == "active")).scalar_one()
            s.execute(text("UPDATE client SET status_id = :sid WHERE status_id IS NULL"), {"sid": status.id})

        if not s.execute(select(Account).where(Account.login == "client")).scalar_one_or_none():
            demo_client = s.execute(select(Client).order_by(Client.id)).scalars().first()
            if not demo_client:
                status = s.execute(select(ClientStatus).where(ClientStatus.code == "active")).scalar_one()
                demo_client = Client(
                    full_name="Клиент Пример",
                    phone="+7 900 111-22-33",
                    email="client@example.com",
                    status_id=status.id,
                )
                s.add(demo_client)
                s.flush()
            s.add(Account(login="client", password_hash=generate_password_hash("client"), role="client", client_id=demo_client.id))

        # демо-зона
        if s.execute(select(func.count(Zone.id))).scalar_one() == 0:
            zt = s.execute(select(ZoneType).where(ZoneType.code == "trampoline")).scalar_one()
            zs = s.execute(select(ZoneStatus).where(ZoneStatus.code == "available")).scalar_one()
            s.add(
                Zone(
                    zone_name="Зона A",
                    type_id=zt.id,
                    capacity=10,
                    base_price=money(800),
                    status_id=zs.id,
                    description="Базовая зона для прыжков",
                )
            )

        # демо-услуги
        if s.execute(select(func.count(Service.id))).scalar_one() == 0:
            s.add(Service(name="Носки антискользящие", base_price=money(150), description="Пара носков"))
            s.add(Service(name="Аренда шкафчика", base_price=money(100), description="На время посещения"))
            s.add(Service(name="Инструктор (30 мин)", base_price=money(500), description="Персональная тренировка"))

        # демо-должность и тренер
        trainer_position = s.execute(select(Position).where(Position.code == "trainer")).scalar_one_or_none()
        if not trainer_position:
            trainer_position = Position(code="trainer", name="Тренер", description="Инструктор на батутной арене")
            s.add(trainer_position)
            s.flush()
        if s.execute(select(func.count(Employee.id))).scalar_one() == 0:
            s.add(
                Employee(
                    full_name="Петрова Анна",
                    position_id=trainer_position.id,
                    phone="+7 900 555-66-77",
                    email="trainer@example.com",
                )
            )

        # демо-слоты расписания
        if s.execute(select(func.count(ScheduleSlot.id))).scalar_one() == 0:
            zone = s.execute(select(Zone).order_by(Zone.id)).scalars().first()
            trainer = s.execute(select(Employee).order_by(Employee.id)).scalars().first()
            if zone:
                now = datetime.now().replace(minute=0, second=0, microsecond=0)
                for i in range(1, 6):
                    start = now.replace(hour=10 + i)
                    s.add(
                        ScheduleSlot(
                            zone_id=zone.id,
                            employee_id=trainer.id if trainer else None,
                            datetime_from=start,
                            datetime_to=start.replace(hour=start.hour + 1),
                            capacity=zone.capacity,
                            price=zone.base_price,
                            lesson_type="group",
                        )
                    )

        s.commit()

# ---- auth ----
@app.get("/login")
def login():
    return render_template("auth/login.html")


@app.post("/login")
def login_post():
    login_ = request.form.get("login", "").strip()
    pwd = request.form.get("password", "")
    with db_session() as s:
        acc = s.execute(select(Account).where(Account.login == login_)).scalar_one_or_none()
        if acc and check_password_hash(acc.password_hash, pwd):
            login_user(acc)
            if acc.role == "client":
                return redirect(url_for("client_dashboard"))
            return redirect(url_for("dashboard"))
    flash("Неверный логин или пароль", "danger")
    return redirect(url_for("login"))


@app.get("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))



# ---- account ----
@app.route("/account/password", methods=["GET", "POST"])
@login_required
def account_password():
    if request.method == "POST":
        current_pwd = request.form.get("current_password", "")
        new_pwd = request.form.get("new_password", "")
        new_pwd2 = request.form.get("new_password2", "")

        if len(new_pwd) < 6:
            flash("Новый пароль должен быть минимум 6 символов", "warning")
            return redirect(url_for("account_password"))
        if new_pwd != new_pwd2:
            flash("Новые пароли не совпадают", "danger")
            return redirect(url_for("account_password"))

        with db_session() as s:
            acc = s.get(Account, int(current_user.get_id()))
            if not acc or not check_password_hash(acc.password_hash, current_pwd):
                flash("Текущий пароль неверный", "danger")
                return redirect(url_for("account_password"))

            acc.password_hash = generate_password_hash(new_pwd)
            s.commit()

        flash("Пароль обновлён", "success")
        return redirect(url_for("dashboard"))

    return render_template("account/password.html")


# ---- client auth & dashboard ----
@app.route("/client/register", methods=["GET", "POST"])
def client_register():
    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        phone = request.form.get("phone", "").strip() or None
        email = request.form.get("email", "").strip() or None
        dob_raw = request.form.get("dob", "").strip()
        login_ = request.form.get("login", "").strip()
        pwd = request.form.get("password", "")
        pwd2 = request.form.get("password2", "")

        if not full_name or not login_ or not pwd:
            flash("Заполни обязательные поля", "warning")
            return redirect(url_for("client_register"))
        if pwd != pwd2:
            flash("Пароли не совпадают", "danger")
            return redirect(url_for("client_register"))

        dob = datetime.strptime(dob_raw, "%Y-%m-%d").date() if dob_raw else None

        with db_session() as s:
            if s.execute(select(Account).where(Account.login == login_)).scalar_one_or_none():
                flash("Такой логин уже занят", "danger")
                return redirect(url_for("client_register"))
            status = s.execute(select(ClientStatus).where(ClientStatus.code == "active")).scalar_one()
            client = Client(full_name=full_name, phone=phone, email=email, dob=dob, status_id=status.id)
            s.add(client)
            s.flush()
            acc = Account(login=login_, password_hash=generate_password_hash(pwd), role="client", client_id=client.id)
            s.add(acc)
            s.commit()
            login_user(acc)
        flash("Учётная запись создана", "success")
        return redirect(url_for("client_dashboard"))

    return render_template("client/register.html")


@app.get("/client")
@login_required
@client_required
def client_dashboard():
    with db_session() as s:
        client = (
            s.execute(select(Client).options(joinedload(Client.status)).where(Client.id == current_user.client_id))
            .scalar_one()
        )
        upcoming = (
            s.execute(
                select(Booking)
                .options(joinedload(Booking.zone), joinedload(Booking.status))
                .where(Booking.client_id == client.id)
                .order_by(Booking.datetime_from.asc())
                .limit(5)
            )
            .scalars()
            .all()
        )
        subscriptions = (
            s.execute(
                select(Subscription)
                .options(joinedload(Subscription.status), joinedload(Subscription.service))
                .where(Subscription.client_id == client.id)
                .order_by(Subscription.end_date.asc())
            )
            .scalars()
            .all()
        )
        notifications = (
            s.execute(
                select(Notification)
                .where(Notification.client_id == client.id)
                .order_by(Notification.created_at.desc())
                .limit(5)
            )
            .scalars()
            .all()
        )
    return render_template(
        "client/dashboard.html",
        client=client,
        upcoming=upcoming,
        subscriptions=subscriptions,
        notifications=notifications,
    )


def _client_schedule_filters():
    date_raw = request.args.get("date", "").strip()
    time_from = request.args.get("time_from", "").strip()
    time_to = request.args.get("time_to", "").strip()
    zone_id = request.args.get("zone_id", "").strip()
    lesson_type = request.args.get("lesson_type", "").strip()
    employee_id = request.args.get("employee_id", "").strip()
    return date_raw, time_from, time_to, zone_id, lesson_type, employee_id


@app.get("/client/schedule")
@login_required
@client_required
def client_schedule():
    date_raw, time_from, time_to, zone_id, lesson_type, employee_id = _client_schedule_filters()
    with db_session() as s:
        query = select(ScheduleSlot).options(joinedload(ScheduleSlot.zone), joinedload(ScheduleSlot.employee)).where(
            ScheduleSlot.is_active.is_(True)
        )
        if date_raw:
            query = query.where(func.date(ScheduleSlot.datetime_from) == date_raw)
        if time_from:
            query = query.where(func.strftime("%H:%M", ScheduleSlot.datetime_from) >= time_from)
        if time_to:
            query = query.where(func.strftime("%H:%M", ScheduleSlot.datetime_from) <= time_to)
        if zone_id and zone_id.isdigit():
            query = query.where(ScheduleSlot.zone_id == int(zone_id))
        if lesson_type:
            query = query.where(ScheduleSlot.lesson_type == lesson_type)
        if employee_id and employee_id.isdigit():
            query = query.where(ScheduleSlot.employee_id == int(employee_id))

        slots = s.execute(query.order_by(ScheduleSlot.datetime_from.asc())).scalars().all()
        slot_ids = [slot.id for slot in slots]
        booked_map = {}
        if slot_ids:
            booked_rows = s.execute(
                select(Booking.schedule_slot_id, func.coalesce(func.sum(Booking.participants_count), 0))
                .join(Booking.status)
                .where(Booking.schedule_slot_id.in_(slot_ids), BookingStatus.code != "cancelled")
                .group_by(Booking.schedule_slot_id)
            ).all()
            booked_map = {slot_id: qty for slot_id, qty in booked_rows}
        zones = s.execute(select(Zone).order_by(Zone.zone_name)).scalars().all()
        employees = s.execute(select(Employee).order_by(Employee.full_name)).scalars().all()
    return render_template(
        "client/schedule.html",
        slots=slots,
        booked_map=booked_map,
        zones=zones,
        employees=employees,
        filters={
            "date": date_raw,
            "time_from": time_from,
            "time_to": time_to,
            "zone_id": zone_id,
            "lesson_type": lesson_type,
            "employee_id": employee_id,
        },
    )


@app.route("/client/schedule/<int:slot_id>/book", methods=["GET", "POST"])
@login_required
@client_required
def client_booking_create(slot_id: int):
    with db_session() as s:
        slot = (
            s.execute(
                select(ScheduleSlot)
                .options(joinedload(ScheduleSlot.zone), joinedload(ScheduleSlot.employee))
                .where(ScheduleSlot.id == slot_id)
            )
            .scalar_one_or_none()
        )
        if not slot:
            flash("Слот не найден", "danger")
            return redirect(url_for("client_schedule"))

        booked = (
            s.execute(
                select(func.coalesce(func.sum(Booking.participants_count), 0))
                .join(Booking.status)
                .where(Booking.schedule_slot_id == slot_id, BookingStatus.code != "cancelled")
            ).scalar_one()
            or 0
        )
        available = max(slot.capacity - int(booked), 0)
        services = s.execute(select(Service).order_by(Service.name)).scalars().all()
        subscriptions = (
            s.execute(
                select(Subscription)
                .options(joinedload(Subscription.status), joinedload(Subscription.service))
                .where(
                    Subscription.client_id == current_user.client_id,
                    Subscription.remaining_visits > 0,
                    Subscription.end_date >= datetime.utcnow().date(),
                )
                .order_by(Subscription.end_date.asc())
            )
            .scalars()
            .all()
        )
        subscription_map = {sub.id: sub for sub in subscriptions}

        if request.method == "POST":
            participants_raw = request.form.get("participants_count", "1")
            subscription_id = request.form.get("subscription_id") or None
            try:
                participants = int(participants_raw)
            except ValueError:
                participants = 1
            if participants <= 0:
                participants = 1
            if participants > available:
                flash("Недостаточно свободных мест", "warning")
                return redirect(url_for("client_booking_create", slot_id=slot_id))

            status = s.execute(select(BookingStatus).where(BookingStatus.code == "new")).scalar_one()
            session_sum = money(Decimal(str(slot.price)) * participants)
            subscription = None
            if subscription_id:
                subscription = subscription_map.get(int(subscription_id)) if subscription_id.isdigit() else None
                if not subscription or subscription.remaining_visits < participants:
                    flash("Недостаточно посещений в абонементе", "warning")
                    return redirect(url_for("client_booking_create", slot_id=slot_id))
                subscription.remaining_visits -= participants
                session_sum = money(0)

            booking = Booking(
                client_id=current_user.client_id,
                zone_id=slot.zone_id,
                schedule_slot_id=slot.id,
                subscription_id=subscription.id if subscription else None,
                datetime_from=slot.datetime_from,
                datetime_to=slot.datetime_to,
                participants_count=participants,
                session_sum=session_sum,
                total_sum=session_sum,
                status_id=status.id,
            )
            s.add(booking)
            s.flush()

            for service in services:
                qty_raw = request.form.get(f"service_{service.id}_qty", "").strip()
                if not qty_raw:
                    continue
                try:
                    qty = int(qty_raw)
                except ValueError:
                    continue
                if qty <= 0:
                    continue
                line_sum = money(Decimal(str(service.base_price)) * qty)
                s.add(
                    BookingService(
                        booking_id=booking.id,
                        service_id=service.id,
                        qty=qty,
                        unit_price=service.base_price,
                        line_sum=line_sum,
                    )
                )

            recalc_booking_total(s, booking.id)
            s.add(
                Notification(
                    client_id=current_user.client_id,
                    message=f"Бронь №{booking.id} создана и ожидает оплаты.",
                )
            )
            s.commit()
            flash("Бронь создана", "success")
            return redirect(url_for("client_booking_view", booking_id=booking.id))

    return render_template(
        "client/booking_create.html",
        slot=slot,
        available=available,
        services=services,
        subscriptions=subscriptions,
    )


@app.get("/client/bookings")
@login_required
@client_required
def client_bookings():
    with db_session() as s:
        bookings = (
            s.execute(
                select(Booking)
                .options(
                    joinedload(Booking.zone),
                    joinedload(Booking.status),
                    joinedload(Booking.visit),
                    joinedload(Booking.schedule_slot).joinedload(ScheduleSlot.employee),
                )
                .where(Booking.client_id == current_user.client_id)
                .order_by(Booking.datetime_from.desc())
            )
            .scalars()
            .all()
        )
        paid_rows = s.execute(
            select(Payment.booking_id, func.coalesce(func.sum(Payment.amount), 0))
            .where(Payment.booking_id.in_([b.id for b in bookings] or [0]))
            .group_by(Payment.booking_id)
        ).all()
        paid_map = {bid: total for bid, total in paid_rows}
    now = datetime.utcnow()
    visit_status_map = {}
    for booking in bookings:
        if booking.status and booking.status.code == "cancelled":
            visit_status = "Отменено"
        elif booking.visit and booking.visit.checkout_at:
            visit_status = "Прошло"
        elif booking.datetime_to < now:
            visit_status = "Неявка"
        else:
            visit_status = "Запланировано"
        visit_status_map[booking.id] = visit_status
    return render_template(
        "client/bookings.html",
        bookings=bookings,
        paid_map=paid_map,
        visit_status_map=visit_status_map,
    )


@app.get("/client/bookings/<int:booking_id>")
@login_required
@client_required
def client_booking_view(booking_id: int):
    with db_session() as s:
        booking = (
            s.execute(
                select(Booking)
                .options(
                    joinedload(Booking.zone),
                    joinedload(Booking.status),
                    joinedload(Booking.services).joinedload(BookingService.service),
                    joinedload(Booking.payments),
                    joinedload(Booking.subscription).joinedload(Subscription.service),
                )
                .where(Booking.id == booking_id, Booking.client_id == current_user.client_id)
            )
            .scalar_one_or_none()
        )
        if not booking:
            flash("Бронь не найдена", "danger")
            return redirect(url_for("client_bookings"))
        paid = sum(float(p.amount) for p in booking.payments)
    return render_template("client/booking_view.html", booking=booking, paid=paid)


@app.route("/client/bookings/<int:booking_id>/pay", methods=["GET", "POST"])
@login_required
@client_required
def client_booking_pay(booking_id: int):
    with db_session() as s:
        booking = (
            s.execute(
                select(Booking)
                .options(joinedload(Booking.payments), joinedload(Booking.status))
                .where(Booking.id == booking_id, Booking.client_id == current_user.client_id)
            )
            .scalar_one_or_none()
        )
        if not booking:
            flash("Бронь не найдена", "danger")
            return redirect(url_for("client_bookings"))
        total_paid = sum(float(p.amount) for p in booking.payments)
        total_sum = float(booking.total_sum or 0)
        due = max(total_sum - total_paid, 0.0)

        if request.method == "POST":
            method = request.form.get("method", "card")
            if due <= 0:
                flash("Бронь уже оплачена", "warning")
                return redirect(url_for("client_booking_view", booking_id=booking_id))
            payment = Payment(booking_id=booking.id, amount=money(due), method=method)
            s.add(payment)
            status = s.execute(select(BookingStatus).where(BookingStatus.code == "confirmed")).scalar_one()
            booking.status_id = status.id
            s.add(
                Notification(
                    client_id=current_user.client_id,
                    message=f"Оплата по брони №{booking.id} принята. Бронь подтверждена.",
                )
            )
            s.commit()
            flash("Оплата прошла успешно", "success")
            return redirect(url_for("client_booking_view", booking_id=booking_id))

    return render_template(
        "client/booking_pay.html",
        booking=booking,
        total_sum=total_sum,
        total_paid=total_paid,
        due=due,
    )


@app.route("/client/profile", methods=["GET", "POST"])
@login_required
@client_required
def client_profile():
    with db_session() as s:
        client = s.get(Client, current_user.client_id)
        if request.method == "POST":
            client.full_name = request.form.get("full_name", "").strip()
            client.phone = request.form.get("phone", "").strip() or None
            client.email = request.form.get("email", "").strip() or None
            dob_raw = request.form.get("dob", "").strip()
            client.dob = datetime.strptime(dob_raw, "%Y-%m-%d").date() if dob_raw else None
            s.commit()
            flash("Профиль обновлён", "success")
            return redirect(url_for("client_profile"))
        status = client.status.name if client.status else "Не задан"
    return render_template("client/profile.html", client=client, status=status)


@app.route("/client/subscriptions", methods=["GET"])
@login_required
@client_required
def client_subscriptions():
    with db_session() as s:
        subs = (
            s.execute(
                select(Subscription)
                .options(joinedload(Subscription.status), joinedload(Subscription.service))
                .where(Subscription.client_id == current_user.client_id)
                .order_by(Subscription.end_date.desc())
            )
            .scalars()
            .all()
        )
    return render_template("client/subscriptions.html", subscriptions=subs)


@app.route("/client/subscriptions/purchase", methods=["GET", "POST"])
@login_required
@client_required
def client_subscription_purchase():
    with db_session() as s:
        services = s.execute(select(Service).order_by(Service.name)).scalars().all()
        status = s.execute(select(SubscriptionStatus).where(SubscriptionStatus.code == "active")).scalar_one()

        if request.method == "POST":
            service_id = request.form.get("service_id") or None
            visits_raw = request.form.get("visits", "5")
            duration_raw = request.form.get("duration_days", "30")
            try:
                visits = int(visits_raw)
            except ValueError:
                visits = 5
            try:
                duration_days = int(duration_raw)
            except ValueError:
                duration_days = 30

            start_date = datetime.utcnow().date()
            end_date = start_date + timedelta(days=duration_days)
            subscription = Subscription(
                client_id=current_user.client_id,
                service_id=int(service_id) if service_id and service_id.isdigit() else None,
                start_date=start_date,
                end_date=end_date,
                total_visits=visits,
                remaining_visits=visits,
                status_id=status.id,
            )
            s.add(subscription)
            s.add(
                Notification(
                    client_id=current_user.client_id,
                    message="Абонемент активирован. Следите за остатком посещений.",
                )
            )
            s.commit()
            flash("Абонемент оформлен", "success")
            return redirect(url_for("client_subscriptions"))

    return render_template("client/subscription_purchase.html", services=services)


@app.route("/client/notifications", methods=["GET", "POST"])
@login_required
@client_required
def client_notifications():
    with db_session() as s:
        if request.method == "POST":
            s.execute(
                text("UPDATE notification SET is_read = 1 WHERE client_id = :cid"),
                {"cid": current_user.client_id},
            )
            s.commit()
            flash("Уведомления отмечены как прочитанные", "success")
            return redirect(url_for("client_notifications"))
        notifications = (
            s.execute(
                select(Notification)
                .where(Notification.client_id == current_user.client_id)
                .order_by(Notification.created_at.desc())
            )
            .scalars()
            .all()
        )
    return render_template("client/notifications.html", notifications=notifications)
# ---- dashboard ----
@app.get("/")
@login_required
def dashboard():
    if current_user.role == "client":
        return redirect(url_for("client_dashboard"))
    with db_session() as s:
        stats = {
            "zones": s.execute(select(func.count(Zone.id))).scalar_one(),
            "clients": s.execute(select(func.count(Client.id))).scalar_one(),
            "bookings": s.execute(select(func.count(Booking.id))).scalar_one(),
            "payments": s.execute(select(func.count(Payment.id))).scalar_one(),
        }
        latest = (
            s.execute(
                select(Booking)
                .options(joinedload(Booking.client), joinedload(Booking.zone), joinedload(Booking.status))
                .order_by(Booking.id.desc())
                .limit(10)
            )
            .scalars()
            .all()
        )
    return render_template("dashboard.html", stats=stats, latest=latest)


# ---- generic render helpers ----
def render_list(title: str, headers: list[str], rows: list[dict], create_url: str, subtitle: Optional[str] = None, active: str = ''):
    class R:
        def __init__(self, cells, edit_url, delete_url):
            self.cells = cells
            self.edit_url = edit_url
            self.delete_url = delete_url

    rlist = [R(**r) for r in rows]
    return render_template("common/list.html", title=title, subtitle=subtitle, headers=headers, rows=rlist, create_url=create_url, active=active)


def render_form(title: str, fields: list[dict], back_url: str, subtitle: Optional[str] = None, active: str = ''):
    return render_template("common/form.html", title=title, subtitle=subtitle, fields=fields, back_url=back_url, active=active)


# ---- ZoneType ----
@app.get("/zone-types")
@login_required
@admin_required
def zone_types_list():
    with db_session() as s:
        items = s.execute(select(ZoneType).order_by(ZoneType.id.desc())).scalars().all()
    rows = [{
        "cells": [it.id, it.code, it.name, (it.description or "")],
        "edit_url": url_for("zone_type_edit", item_id=it.id),
        "delete_url": url_for("zone_type_delete", item_id=it.id),
    } for it in items]
    return render_list("Типы зон", ["ID", "Код", "Название", "Описание"], rows, url_for("zone_type_create"), active="zone_types")


@app.route("/zone-types/create", methods=["GET", "POST"])
@login_required
@admin_required
def zone_type_create():
    if request.method == "POST":
        code = request.form.get("code", "").strip()
        name = request.form.get("name", "").strip()
        desc = request.form.get("description", "").strip() or None
        if not code or not name:
            flash("Заполни код и название", "warning")
            return redirect(url_for("zone_type_create"))
        with db_session() as s:
            if s.execute(select(ZoneType).where(ZoneType.code == code)).scalar_one_or_none():
                flash("Такой код уже существует", "danger")
                return redirect(url_for("zone_type_create"))
            s.add(ZoneType(code=code, name=name, description=desc))
            s.commit()
        flash("Тип зоны добавлен", "success")
        return redirect(url_for("zone_types_list"))

    fields = [
        {"name": "code", "label": "Код", "type": "text", "required": True, "col": "col-md-6"},
        {"name": "name", "label": "Название", "type": "text", "required": True, "col": "col-md-6"},
        {"name": "description", "label": "Описание", "type": "textarea", "required": False},
    ]
    return render_form("Добавить тип зоны", fields, url_for("zone_types_list"), active="zone_types")


@app.route("/zone-types/<int:item_id>/edit", methods=["GET", "POST"])
@login_required
@admin_required
def zone_type_edit(item_id: int):
    with db_session() as s:
        it = s.get(ZoneType, item_id)
        if not it:
            flash("Не найдено", "danger")
            return redirect(url_for("zone_types_list"))
        if request.method == "POST":
            it.code = request.form.get("code", "").strip()
            it.name = request.form.get("name", "").strip()
            it.description = request.form.get("description", "").strip() or None
            s.commit()
            flash("Сохранено", "success")
            return redirect(url_for("zone_types_list"))

        fields = [
            {"name": "code", "label": "Код", "type": "text", "required": True, "value": it.code, "col": "col-md-6"},
            {"name": "name", "label": "Название", "type": "text", "required": True, "value": it.name, "col": "col-md-6"},
            {"name": "description", "label": "Описание", "type": "textarea", "required": False, "value": it.description},
        ]
    return render_form("Редактировать тип зоны", fields, url_for("zone_types_list"), active="zone_types")


@app.post("/zone-types/<int:item_id>/delete")
@login_required
@admin_required
def zone_type_delete(item_id: int):
    with db_session() as s:
        it = s.get(ZoneType, item_id)
        if it:
            s.delete(it)
            s.commit()
            flash("Удалено", "success")
    return redirect(url_for("zone_types_list"))


# ---- ZoneStatus ----
@app.get("/zone-statuses")
@login_required
@admin_required
def zone_statuses_list():
    with db_session() as s:
        items = s.execute(select(ZoneStatus).order_by(ZoneStatus.id.desc())).scalars().all()
    rows = [{
        "cells": [it.id, it.code, it.name],
        "edit_url": url_for("zone_status_edit", item_id=it.id),
        "delete_url": url_for("zone_status_delete", item_id=it.id),
    } for it in items]
    return render_list("Статусы зон", ["ID", "Код", "Название"], rows, url_for("zone_status_create"), active="zone_statuses")


@app.route("/zone-statuses/create", methods=["GET", "POST"])
@login_required
@admin_required
def zone_status_create():
    if request.method == "POST":
        code = request.form.get("code", "").strip()
        name = request.form.get("name", "").strip()
        if not code or not name:
            flash("Заполни код и название", "warning")
            return redirect(url_for("zone_status_create"))
        with db_session() as s:
            if s.execute(select(ZoneStatus).where(ZoneStatus.code == code)).scalar_one_or_none():
                flash("Такой код уже существует", "danger")
                return redirect(url_for("zone_status_create"))
            s.add(ZoneStatus(code=code, name=name))
            s.commit()
        flash("Статус зоны добавлен", "success")
        return redirect(url_for("zone_statuses_list"))

    fields = [
        {"name": "code", "label": "Код", "type": "text", "required": True, "col": "col-md-6"},
        {"name": "name", "label": "Название", "type": "text", "required": True, "col": "col-md-6"},
    ]
    return render_form("Добавить статус зоны", fields, url_for("zone_statuses_list"), active="zone_statuses")


@app.route("/zone-statuses/<int:item_id>/edit", methods=["GET", "POST"])
@login_required
@admin_required
def zone_status_edit(item_id: int):
    with db_session() as s:
        it = s.get(ZoneStatus, item_id)
        if not it:
            flash("Не найдено", "danger")
            return redirect(url_for("zone_statuses_list"))
        if request.method == "POST":
            it.code = request.form.get("code", "").strip()
            it.name = request.form.get("name", "").strip()
            s.commit()
            flash("Сохранено", "success")
            return redirect(url_for("zone_statuses_list"))

        fields = [
            {"name": "code", "label": "Код", "type": "text", "required": True, "value": it.code, "col": "col-md-6"},
            {"name": "name", "label": "Название", "type": "text", "required": True, "value": it.name, "col": "col-md-6"},
        ]
    return render_form("Редактировать статус зоны", fields, url_for("zone_statuses_list"), active="zone_statuses")


@app.post("/zone-statuses/<int:item_id>/delete")
@login_required
@admin_required
def zone_status_delete(item_id: int):
    with db_session() as s:
        it = s.get(ZoneStatus, item_id)
        if it:
            s.delete(it)
            s.commit()
            flash("Удалено", "success")
    return redirect(url_for("zone_statuses_list"))


# ---- Zones ----
@app.get("/zones")
@login_required
@admin_required
def zones_list():
    with db_session() as s:
        items = (
            s.execute(select(Zone).options(joinedload(Zone.type), joinedload(Zone.status)).order_by(Zone.id.desc()))
            .scalars()
            .all()
        )
    rows = [{
        "cells": [
            it.id,
            it.zone_name,
            f"{it.type.name} <span class='badge badge-soft ms-1'>{it.type.code}</span>",
            it.capacity,
            f"{float(it.base_price):.2f}",
            it.status.name
        ],
        "edit_url": url_for("zone_edit", item_id=it.id),
        "delete_url": url_for("zone_delete", item_id=it.id),
    } for it in items]
    return render_list("Зоны", ["ID", "Название", "Тип", "Вместимость", "Базовая цена", "Статус"], rows, url_for("zone_create"), active="zones")


@app.route("/zones/create", methods=["GET", "POST"])
@login_required
@admin_required
def zone_create():
    with db_session() as s:
        ztypes = s.execute(select(ZoneType).order_by(ZoneType.name)).scalars().all()
        zstats = s.execute(select(ZoneStatus).order_by(ZoneStatus.name)).scalars().all()

        if request.method == "POST":
            zone_name = request.form.get("zone_name", "").strip()
            type_id = int(request.form.get("type_id"))
            status_id = int(request.form.get("status_id"))
            capacity = int(request.form.get("capacity"))
            base_price = money(request.form.get("base_price", "0"))
            desc = request.form.get("description", "").strip() or None

            if not zone_name or capacity <= 0:
                flash("Заполни название и вместимость", "warning")
                return redirect(url_for("zone_create"))

            if s.execute(select(Zone).where(Zone.zone_name == zone_name)).scalar_one_or_none():
                flash("Зона с таким названием уже есть", "danger")
                return redirect(url_for("zone_create"))

            s.add(Zone(zone_name=zone_name, type_id=type_id, status_id=status_id,
                       capacity=capacity, base_price=base_price, description=desc))
            s.commit()
            flash("Зона добавлена", "success")
            return redirect(url_for("zones_list"))

    fields = [
        {"name": "zone_name", "label": "Название зоны", "type": "text", "required": True, "col": "col-md-6"},
        {"name": "capacity", "label": "Вместимость (чел)", "type": "number", "required": True, "col": "col-md-3", "help": "Напр. 10"},
        {"name": "base_price", "label": "Базовая цена (за 1 час)", "type": "number", "required": True, "col": "col-md-3", "help": "Напр. 800.00"},
        {"name": "type_id", "label": "Тип зоны", "type": "select", "required": True, "col": "col-md-6",
         "options": [{"value": t.id, "label": f"{t.name} ({t.code})"} for t in ztypes]},
        {"name": "status_id", "label": "Статус", "type": "select", "required": True, "col": "col-md-6",
         "options": [{"value": st.id, "label": f"{st.name} ({st.code})"} for st in zstats]},
        {"name": "description", "label": "Описание", "type": "textarea", "required": False},
    ]
    return render_form("Добавить зону", fields, url_for("zones_list"), active="zones")


@app.route("/zones/<int:item_id>/edit", methods=["GET", "POST"])
@login_required
@admin_required
def zone_edit(item_id: int):
    with db_session() as s:
        it = s.get(Zone, item_id)
        if not it:
            flash("Не найдено", "danger")
            return redirect(url_for("zones_list"))

        ztypes = s.execute(select(ZoneType).order_by(ZoneType.name)).scalars().all()
        zstats = s.execute(select(ZoneStatus).order_by(ZoneStatus.name)).scalars().all()

        if request.method == "POST":
            it.zone_name = request.form.get("zone_name", "").strip()
            it.capacity = int(request.form.get("capacity"))
            it.base_price = money(request.form.get("base_price", "0"))
            it.type_id = int(request.form.get("type_id"))
            it.status_id = int(request.form.get("status_id"))
            it.description = request.form.get("description", "").strip() or None
            s.commit()
            flash("Сохранено", "success")
            return redirect(url_for("zones_list"))

        fields = [
            {"name": "zone_name", "label": "Название зоны", "type": "text", "required": True, "value": it.zone_name, "col": "col-md-6"},
            {"name": "capacity", "label": "Вместимость (чел)", "type": "number", "required": True, "value": it.capacity, "col": "col-md-3"},
            {"name": "base_price", "label": "Базовая цена (за 1 час)", "type": "number", "required": True, "value": float(it.base_price), "col": "col-md-3"},
            {"name": "type_id", "label": "Тип зоны", "type": "select", "required": True, "value": it.type_id, "col": "col-md-6",
             "options": [{"value": t.id, "label": f"{t.name} ({t.code})"} for t in ztypes]},
            {"name": "status_id", "label": "Статус", "type": "select", "required": True, "value": it.status_id, "col": "col-md-6",
             "options": [{"value": st.id, "label": f"{st.name} ({st.code})"} for st in zstats]},
            {"name": "description", "label": "Описание", "type": "textarea", "required": False, "value": it.description},
        ]
    return render_form("Редактировать зону", fields, url_for("zones_list"), active="zones")


@app.post("/zones/<int:item_id>/delete")
@login_required
@admin_required
def zone_delete(item_id: int):
    with db_session() as s:
        it = s.get(Zone, item_id)
        if it:
            s.delete(it)
            s.commit()
            flash("Удалено", "success")
    return redirect(url_for("zones_list"))


# ---- BookingStatus ----
@app.get("/booking-statuses")
@login_required
@admin_required
def booking_statuses_list():
    with db_session() as s:
        items = s.execute(select(BookingStatus).order_by(BookingStatus.id.desc())).scalars().all()
    rows = [{
        "cells": [it.id, it.code, it.name],
        "edit_url": url_for("booking_status_edit", item_id=it.id),
        "delete_url": url_for("booking_status_delete", item_id=it.id),
    } for it in items]
    return render_list("Статусы брони", ["ID", "Код", "Название"], rows, url_for("booking_status_create"), active="bookings")


@app.route("/booking-statuses/create", methods=["GET", "POST"])
@login_required
@admin_required
def booking_status_create():
    if request.method == "POST":
        code = request.form.get("code", "").strip()
        name = request.form.get("name", "").strip()
        if not code or not name:
            flash("Заполни код и название", "warning")
            return redirect(url_for("booking_status_create"))
        with db_session() as s:
            if s.execute(select(BookingStatus).where(BookingStatus.code == code)).scalar_one_or_none():
                flash("Такой код уже существует", "danger")
                return redirect(url_for("booking_status_create"))
            s.add(BookingStatus(code=code, name=name))
            s.commit()
        flash("Статус брони добавлен", "success")
        return redirect(url_for("booking_statuses_list"))

    fields = [
        {"name": "code", "label": "Код", "type": "text", "required": True, "col": "col-md-6"},
        {"name": "name", "label": "Название", "type": "text", "required": True, "col": "col-md-6"},
    ]
    return render_form("Добавить статус брони", fields, url_for("booking_statuses_list"), active="bookings")


@app.route("/booking-statuses/<int:item_id>/edit", methods=["GET", "POST"])
@login_required
@admin_required
def booking_status_edit(item_id: int):
    with db_session() as s:
        it = s.get(BookingStatus, item_id)
        if not it:
            flash("Не найдено", "danger")
            return redirect(url_for("booking_statuses_list"))
        if request.method == "POST":
            it.code = request.form.get("code", "").strip()
            it.name = request.form.get("name", "").strip()
            s.commit()
            flash("Сохранено", "success")
            return redirect(url_for("booking_statuses_list"))

        fields = [
            {"name": "code", "label": "Код", "type": "text", "required": True, "value": it.code, "col": "col-md-6"},
            {"name": "name", "label": "Название", "type": "text", "required": True, "value": it.name, "col": "col-md-6"},
        ]
    return render_form("Редактировать статус брони", fields, url_for("booking_statuses_list"), active="bookings")


@app.post("/booking-statuses/<int:item_id>/delete")
@login_required
@admin_required
def booking_status_delete(item_id: int):
    with db_session() as s:
        it = s.get(BookingStatus, item_id)
        if it:
            s.delete(it)
            s.commit()
            flash("Удалено", "success")
    return redirect(url_for("booking_statuses_list"))


# ---- Services ----
@app.get("/services")
@login_required
@admin_required
def services_list():
    with db_session() as s:
        items = s.execute(select(Service).order_by(Service.id.desc())).scalars().all()
    rows = [{
        "cells": [it.id, it.name, f"{float(it.base_price):.2f}", (it.description or "")],
        "edit_url": url_for("service_edit", item_id=it.id),
        "delete_url": url_for("service_delete", item_id=it.id),
    } for it in items]
    return render_list("Услуги", ["ID", "Название", "Цена", "Описание"], rows, url_for("service_create"), active="services")


@app.route("/services/create", methods=["GET", "POST"])
@login_required
@admin_required
def service_create():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        base_price = money(request.form.get("base_price", "0"))
        desc = request.form.get("description", "").strip() or None
        if not name:
            flash("Заполни название", "warning")
            return redirect(url_for("service_create"))
        with db_session() as s:
            s.add(Service(name=name, base_price=base_price, description=desc))
            s.commit()
        flash("Услуга добавлена", "success")
        return redirect(url_for("services_list"))

    fields = [
        {"name": "name", "label": "Название услуги", "type": "text", "required": True, "col": "col-md-8"},
        {"name": "base_price", "label": "Цена", "type": "number", "required": True, "col": "col-md-4"},
        {"name": "description", "label": "Описание", "type": "textarea", "required": False},
    ]
    return render_form("Добавить услугу", fields, url_for("services_list"), active="services")


@app.route("/services/<int:item_id>/edit", methods=["GET", "POST"])
@login_required
@admin_required
def service_edit(item_id: int):
    with db_session() as s:
        it = s.get(Service, item_id)
        if not it:
            flash("Не найдено", "danger")
            return redirect(url_for("services_list"))
        if request.method == "POST":
            it.name = request.form.get("name", "").strip()
            it.base_price = money(request.form.get("base_price", "0"))
            it.description = request.form.get("description", "").strip() or None
            s.commit()
            flash("Сохранено", "success")
            return redirect(url_for("services_list"))

        fields = [
            {"name": "name", "label": "Название услуги", "type": "text", "required": True, "value": it.name, "col": "col-md-8"},
            {"name": "base_price", "label": "Цена", "type": "number", "required": True, "value": float(it.base_price), "col": "col-md-4"},
            {"name": "description", "label": "Описание", "type": "textarea", "required": False, "value": it.description},
        ]
    return render_form("Редактировать услугу", fields, url_for("services_list"), active="services")


@app.post("/services/<int:item_id>/delete")
@login_required
@admin_required
def service_delete(item_id: int):
    with db_session() as s:
        it = s.get(Service, item_id)
        if it:
            s.delete(it)
            s.commit()
            flash("Удалено", "success")
    return redirect(url_for("services_list"))


# ---- Clients ----
@app.get("/clients")
@login_required
@admin_required
def clients_list():
    with db_session() as s:
        items = s.execute(select(Client).order_by(Client.id.desc())).scalars().all()
    rows = [{
        "cells": [it.id, it.full_name, (it.phone or ""), (it.email or ""), (it.note or "")],
        "edit_url": url_for("client_edit", item_id=it.id),
        "delete_url": url_for("client_delete", item_id=it.id),
    } for it in items]
    return render_list("Клиенты", ["ID", "ФИО", "Телефон", "Email", "Примечание"], rows, url_for("client_create"), active="clients")


@app.route("/clients/create", methods=["GET", "POST"])
@login_required
@admin_required
def client_create():
    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        phone = request.form.get("phone", "").strip() or None
        email = request.form.get("email", "").strip() or None
        note = request.form.get("note", "").strip() or None
        if not full_name:
            flash("Заполни ФИО", "warning")
            return redirect(url_for("client_create"))
        with db_session() as s:
            s.add(Client(full_name=full_name, phone=phone, email=email, note=note))
            s.commit()
        flash("Клиент добавлен", "success")
        return redirect(url_for("clients_list"))

    fields = [
        {"name": "full_name", "label": "ФИО", "type": "text", "required": True, "col": "col-md-8"},
        {"name": "phone", "label": "Телефон", "type": "text", "required": False, "col": "col-md-4"},
        {"name": "email", "label": "Email", "type": "email", "required": False, "col": "col-md-6"},
        {"name": "note", "label": "Примечание", "type": "textarea", "required": False, "col": "col-md-6"},
    ]
    return render_form("Добавить клиента", fields, url_for("clients_list"), active="clients")


@app.route("/clients/<int:item_id>/edit", methods=["GET", "POST"])
@login_required
@admin_required
def client_edit(item_id: int):
    with db_session() as s:
        it = s.get(Client, item_id)
        if not it:
            flash("Не найдено", "danger")
            return redirect(url_for("clients_list"))
        if request.method == "POST":
            it.full_name = request.form.get("full_name", "").strip()
            it.phone = request.form.get("phone", "").strip() or None
            it.email = request.form.get("email", "").strip() or None
            it.note = request.form.get("note", "").strip() or None
            s.commit()
            flash("Сохранено", "success")
            return redirect(url_for("clients_list"))

        fields = [
            {"name": "full_name", "label": "ФИО", "type": "text", "required": True, "value": it.full_name, "col": "col-md-8"},
            {"name": "phone", "label": "Телефон", "type": "text", "required": False, "value": it.phone, "col": "col-md-4"},
            {"name": "email", "label": "Email", "type": "email", "required": False, "value": it.email, "col": "col-md-6"},
            {"name": "note", "label": "Примечание", "type": "textarea", "required": False, "value": it.note, "col": "col-md-6"},
        ]
    return render_form("Редактировать клиента", fields, url_for("clients_list"), active="clients")


@app.post("/clients/<int:item_id>/delete")
@login_required
@admin_required
def client_delete(item_id: int):
    with db_session() as s:
        it = s.get(Client, item_id)
        if it:
            s.delete(it)
            s.commit()
            flash("Удалено", "success")
    return redirect(url_for("clients_list"))


# ---- Bookings ----
@app.get("/bookings")
@login_required
@admin_required
def bookings_list():
    with db_session() as s:
        bookings = (
            s.execute(
                select(Booking)
                .options(joinedload(Booking.client), joinedload(Booking.zone), joinedload(Booking.status))
                .order_by(Booking.id.desc())
                .limit(200)
            )
            .scalars()
            .all()
        )
        paid = s.execute(select(Payment.booking_id, func.coalesce(func.sum(Payment.amount), 0)).group_by(Payment.booking_id)).all()
    paid_map = {bid: float(total) for bid, total in paid}
    return render_template("bookings/list.html", bookings=bookings, paid_map=paid_map)


@app.route("/bookings/create", methods=["GET", "POST"])
@login_required
@admin_required
def booking_create():
    with db_session() as s:
        clients = s.execute(select(Client).order_by(Client.full_name)).scalars().all()
        zones = s.execute(select(Zone).options(joinedload(Zone.type)).order_by(Zone.zone_name)).scalars().all()
        statuses = s.execute(select(BookingStatus).order_by(BookingStatus.id)).scalars().all()

        if request.method == "POST":
            client_id = int(request.form.get("client_id"))
            zone_id = int(request.form.get("zone_id"))
            dt_from = parse_dt_local(request.form.get("dt_from"))
            dt_to = parse_dt_local(request.form.get("dt_to"))
            participants_count = int(request.form.get("participants_count"))
            status_id = int(request.form.get("status_id"))

            if dt_to <= dt_from:
                flash("Конец должен быть позже начала", "danger")
                return redirect(url_for("booking_create"))

            zone = s.get(Zone, zone_id)
            if not zone:
                flash("Зона не найдена", "danger")
                return redirect(url_for("booking_create"))

            if participants_count <= 0 or participants_count > zone.capacity:
                flash(f"Участников должно быть 1..{zone.capacity} (вместимость зоны)", "danger")
                return redirect(url_for("booking_create"))

            cancelled_id = s.execute(select(BookingStatus.id).where(BookingStatus.code == "cancelled")).scalar_one()
            overlap = s.execute(
                select(func.count(Booking.id)).where(
                    Booking.zone_id == zone_id,
                    Booking.status_id != cancelled_id,
                    Booking.datetime_from < dt_to,
                    Booking.datetime_to > dt_from,
                )
            ).scalar_one()

            if overlap and overlap > 0:
                flash("Есть пересечение по времени для выбранной зоны", "danger")
                return redirect(url_for("booking_create"))

            hours = Decimal((dt_to - dt_from).total_seconds()) / Decimal(3600)
            session_sum = money(Decimal(zone.base_price) * hours)
            total_sum = session_sum

            b = Booking(
                client_id=client_id,
                zone_id=zone_id,
                datetime_from=dt_from,
                datetime_to=dt_to,
                participants_count=participants_count,
                session_sum=session_sum,
                total_sum=total_sum,
                status_id=status_id,
            )
            s.add(b)
            s.commit()
            flash("Бронь создана", "success")
            return redirect(url_for("booking_view", booking_id=b.id))

    if not clients:
        flash("Добавь хотя бы одного клиента (Справочники → Клиенты)", "warning")
        return redirect(url_for("clients_list"))
    if not zones:
        flash("Добавь хотя бы одну зону (Справочники → Зоны)", "warning")
        return redirect(url_for("zones_list"))

    return render_template("bookings/create.html", clients=clients, zones=zones, statuses=statuses)



@app.get("/bookings/<int:booking_id>")
@login_required
@admin_required
def booking_view(booking_id: int):
    with db_session() as s:
        b = (
            s.execute(
                select(Booking)
                .where(Booking.id == booking_id)
                .options(
                    joinedload(Booking.client),
                    joinedload(Booking.zone),
                    joinedload(Booking.status),
                    selectinload(Booking.services).selectinload(BookingService.service),
                    joinedload(Booking.visit),
                )
            )
            .scalar_one_or_none()
        )
        if not b:
            flash("Бронь не найдена", "danger")
            return redirect(url_for("bookings_list"))

        payments_db = (
            s.execute(
                select(Payment)
                .where(Payment.booking_id == booking_id)
                .order_by(Payment.id.desc())
            )
            .scalars()
            .all()
        )

        paid_dec = s.execute(
            select(func.coalesce(func.sum(Payment.amount), 0)).where(Payment.booking_id == booking_id)
        ).scalar_one()

        statuses = s.execute(select(BookingStatus).order_by(BookingStatus.id)).scalars().all()
        services_all = s.execute(select(Service).order_by(Service.name)).scalars().all()

    total = float(b.total_sum or 0)
    paid = float(paid_dec or 0)
    due = total - paid

    payments = [
        {"paid_at": p.paid_at, "method": p.method, "amount": float(p.amount or 0)}
        for p in payments_db
    ]

    service_lines = [
        {
            "service_id": ln.service_id,
            "name": ln.service.name,
            "qty": ln.qty,
            "unit_price": float(ln.unit_price),
            "line_sum": float(ln.line_sum),
        }
        for ln in (b.services or [])
    ]


    services_total = sum((ln["line_sum"] for ln in service_lines), 0.0)
    session_total = float(b.session_sum or 0)
    visit = None
    if getattr(b, "visit", None):
        v = b.visit
        visit = {
            "id": v.id,
            "checkin_at": v.checkin_at,
            "checkout_at": v.checkout_at,
            "actual_participants_count": v.actual_participants_count,
        }

    return render_template(
        "bookings/view.html",
        b=b,
        payments=payments,
        paid=paid,
        total=total,
        due=due,
        statuses=statuses,
        services_all=services_all,
        service_lines=service_lines,
        services_total=services_total,
        session_total=session_total,
        visit=visit,
    )



@app.post("/bookings/<int:booking_id>/delete")
@login_required
@admin_required
def booking_delete(booking_id: int):
    with db_session() as s:
        b = s.get(Booking, booking_id)
        if b:
            s.delete(b)
            s.commit()
            flash("Бронь удалена", "success")
    return redirect(url_for("bookings_list"))


@app.post("/bookings/<int:booking_id>/status")
@login_required
@admin_required
def booking_status_change(booking_id: int):
    status_id = int(request.form.get("status_id"))
    with db_session() as s:
        b = s.get(Booking, booking_id)
        if not b:
            flash("Бронь не найдена", "danger")
            return redirect(url_for("bookings_list"))
        b.status_id = status_id
        s.commit()
    flash("Статус обновлён", "success")
    return redirect(url_for("booking_view", booking_id=booking_id))


@app.post("/bookings/<int:booking_id>/payments/add")
@login_required
@admin_required
def payment_add(booking_id: int):
    amount = money(request.form.get("amount", "0"))
    method = request.form.get("method", "cash").strip()
    comment = request.form.get("comment", "").strip() or None
    if amount <= 0:
        flash("Сумма должна быть больше 0", "danger")
        return redirect(url_for("booking_view", booking_id=booking_id))
    with db_session() as s:
        b = s.get(Booking, booking_id)
        if not b:
            flash("Бронь не найдена", "danger")
            return redirect(url_for("bookings_list"))
        s.add(Payment(booking_id=booking_id, amount=amount, method=method, comment=comment, created_by_employee_id=None))
        s.commit()
    flash("Оплата добавлена", "success")
    return redirect(url_for("booking_view", booking_id=booking_id))



# ---- Booking services (booking_service) ----
@app.post("/bookings/<int:booking_id>/services/add")
@login_required
@admin_required
def booking_service_add(booking_id: int):
    service_id = int(request.form.get("service_id"))
    qty = int(request.form.get("qty", "1"))
    unit_price_raw = (request.form.get("unit_price") or "").strip()

    if qty <= 0:
        flash("Количество должно быть больше 0", "danger")
        return redirect(url_for("booking_view", booking_id=booking_id))

    with db_session() as s:
        b = s.get(Booking, booking_id)
        if not b:
            flash("Бронь не найдена", "danger")
            return redirect(url_for("bookings_list"))

        srv = s.get(Service, service_id)
        if not srv:
            flash("Услуга не найдена", "danger")
            return redirect(url_for("booking_view", booking_id=booking_id))

        unit_price = money(unit_price_raw) if unit_price_raw else money(srv.base_price)
        line_sum = money(unit_price * Decimal(qty))

        ln = s.get(BookingService, {"booking_id": booking_id, "service_id": service_id})
        if ln:
            ln.qty = ln.qty + qty
            ln.unit_price = unit_price  # можно оставить как было, но удобнее обновлять на актуальную
            ln.line_sum = money(ln.unit_price * Decimal(ln.qty))
        else:
            s.add(BookingService(booking_id=booking_id, service_id=service_id, qty=qty, unit_price=unit_price, line_sum=line_sum))

        recalc_booking_total(s, booking_id)
        s.commit()

    flash("Услуга добавлена в бронь", "success")
    return redirect(url_for("booking_view", booking_id=booking_id))


@app.post("/bookings/<int:booking_id>/services/<int:service_id>/delete")
@login_required
@admin_required
def booking_service_delete(booking_id: int, service_id: int):
    with db_session() as s:
        ln = s.get(BookingService, {"booking_id": booking_id, "service_id": service_id})
        if ln:
            s.delete(ln)
            recalc_booking_total(s, booking_id)
            s.commit()
            flash("Услуга удалена из брони", "success")
    return redirect(url_for("booking_view", booking_id=booking_id))


# ---- Visit (visit) ----
@app.post("/bookings/<int:booking_id>/visit/checkin")
@login_required
@admin_required
def visit_checkin(booking_id: int):
    apc_raw = (request.form.get("actual_participants_count") or "").strip()
    apc = int(apc_raw) if apc_raw else None

    with db_session() as s:
        b = s.get(Booking, booking_id)
        if not b:
            flash("Бронь не найдена", "danger")
            return redirect(url_for("bookings_list"))

        v = s.execute(select(Visit).where(Visit.booking_id == booking_id)).scalar_one_or_none()
        if not v:
            v = Visit(booking_id=booking_id)
            s.add(v)
            s.flush()

        if v.checkin_at:
            flash("Вход уже оформлен", "warning")
            return redirect(url_for("booking_view", booking_id=booking_id))

        v.checkin_at = datetime.utcnow()
        v.actual_participants_count = apc
        v.opened_by_id = None  # позже свяжем с сотрудником
        s.commit()

    flash("Вход оформлен", "success")
    return redirect(url_for("booking_view", booking_id=booking_id))


@app.post("/bookings/<int:booking_id>/visit/checkout")
@login_required
@admin_required
def visit_checkout(booking_id: int):
    apc_raw = (request.form.get("actual_participants_count") or "").strip()
    apc = int(apc_raw) if apc_raw else None

    with db_session() as s:
        v = s.execute(select(Visit).where(Visit.booking_id == booking_id)).scalar_one_or_none()
        if not v:
            flash("Сначала оформи вход (check-in)", "warning")
            return redirect(url_for("booking_view", booking_id=booking_id))

        if v.checkout_at:
            flash("Выход уже оформлен", "warning")
            return redirect(url_for("booking_view", booking_id=booking_id))

        v.checkout_at = datetime.utcnow()
        if apc is not None:
            v.actual_participants_count = apc
        v.closed_by_id = None  # позже свяжем с сотрудником
        s.commit()

    flash("Выход оформлен", "success")
    return redirect(url_for("booking_view", booking_id=booking_id))


if __name__ == "__main__":
    seed_if_empty()
    app.run(debug=True)
