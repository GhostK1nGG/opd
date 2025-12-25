from __future__ import annotations
from typing import Optional

from datetime import datetime, date
from decimal import Decimal

from flask_login import UserMixin
from sqlalchemy import String, Integer, Date, DateTime, Text, Numeric, ForeignKey, Boolean
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Position(Base):
    __tablename__ = "position"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)

    employees: Mapped[list["Employee"]] = relationship(back_populates="position")


class ZoneType(Base):
    __tablename__ = "zone_type"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)

    zones: Mapped[list["Zone"]] = relationship(back_populates="type")


class ZoneStatus(Base):
    __tablename__ = "zone_status"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)

    zones: Mapped[list["Zone"]] = relationship(back_populates="status")


class BookingStatus(Base):
    __tablename__ = "booking_status"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)

    bookings: Mapped[list["Booking"]] = relationship(back_populates="status")


class Client(Base):
    __tablename__ = "client"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    full_name: Mapped[str] = mapped_column(String, nullable=False)
    dob: Mapped[Optional[date]] = mapped_column(Date)
    phone: Mapped[Optional[str]] = mapped_column(String)
    email: Mapped[Optional[str]] = mapped_column(String)
    note: Mapped[Optional[str]] = mapped_column(Text)
    status_id: Mapped[Optional[int]] = mapped_column(ForeignKey("client_status.id"))

    bookings: Mapped[list["Booking"]] = relationship(back_populates="client")
    accounts: Mapped[list["Account"]] = relationship(back_populates="client")
    status: Mapped[Optional["ClientStatus"]] = relationship(back_populates="clients")
    subscriptions: Mapped[list["Subscription"]] = relationship(back_populates="client")
    notifications: Mapped[list["Notification"]] = relationship(back_populates="client")


class Employee(Base):
    __tablename__ = "employee"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    full_name: Mapped[str] = mapped_column(String, nullable=False)
    position_id: Mapped[int] = mapped_column(ForeignKey("position.id"), nullable=False)
    phone: Mapped[Optional[str]] = mapped_column(String)
    email: Mapped[Optional[str]] = mapped_column(String)
    note: Mapped[Optional[str]] = mapped_column(Text)

    position: Mapped["Position"] = relationship(back_populates="employees")

    opened_visits: Mapped[list["Visit"]] = relationship(
        "Visit",
        foreign_keys="Visit.opened_by_id",
        back_populates="opened_by",
    )
    closed_visits: Mapped[list["Visit"]] = relationship(
        "Visit",
        foreign_keys="Visit.closed_by_id",
        back_populates="closed_by",
    )
    accounts: Mapped[list["Account"]] = relationship(back_populates="employee")


class Account(Base, UserMixin):
    __tablename__ = "account"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    login: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False, default="admin")

    client_id: Mapped[Optional[int]] = mapped_column(ForeignKey("client.id"))
    employee_id: Mapped[Optional[int]] = mapped_column(ForeignKey("employee.id"), nullable=True)
    email_recovery: Mapped[Optional[str]] = mapped_column(String)

    client: Mapped[Optional["Client"]] = relationship(back_populates="accounts")
    employee: Mapped[Optional["Employee"]] = relationship(back_populates="accounts")


class Zone(Base):
    __tablename__ = "zone"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    zone_name: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    type_id: Mapped[int] = mapped_column(ForeignKey("zone_type.id"), nullable=False)
    capacity: Mapped[int] = mapped_column(Integer, nullable=False)
    base_price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    status_id: Mapped[int] = mapped_column(ForeignKey("zone_status.id"), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)

    type: Mapped["ZoneType"] = relationship(back_populates="zones")
    status: Mapped["ZoneStatus"] = relationship(back_populates="zones")
    bookings: Mapped[list["Booking"]] = relationship(back_populates="zone")


class Service(Base):
    __tablename__ = "service"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    base_price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)

    booking_lines: Mapped[list["BookingService"]] = relationship(back_populates="service")


class Booking(Base):
    __tablename__ = "booking"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("client.id"), nullable=False)
    zone_id: Mapped[int] = mapped_column(ForeignKey("zone.id"), nullable=False)
    schedule_slot_id: Mapped[Optional[int]] = mapped_column(ForeignKey("schedule_slot.id"))
    subscription_id: Mapped[Optional[int]] = mapped_column(ForeignKey("subscription.id"))

    datetime_from: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    datetime_to: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    participants_count: Mapped[int] = mapped_column(Integer, nullable=False)

    session_sum: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2))
    total_sum: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2))

    status_id: Mapped[int] = mapped_column(ForeignKey("booking_status.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)

    client: Mapped["Client"] = relationship(back_populates="bookings")
    zone: Mapped["Zone"] = relationship(back_populates="bookings")
    status: Mapped["BookingStatus"] = relationship(back_populates="bookings")
    schedule_slot: Mapped[Optional["ScheduleSlot"]] = relationship(back_populates="bookings")
    subscription: Mapped[Optional["Subscription"]] = relationship(back_populates="bookings")

    payments: Mapped[list["Payment"]] = relationship(back_populates="booking")
    services: Mapped[list["BookingService"]] = relationship(back_populates="booking")
    visit: Mapped[Optional["Visit"]] = relationship(back_populates="booking", uselist=False)


class BookingService(Base):
    __tablename__ = "booking_service"
    booking_id: Mapped[int] = mapped_column(ForeignKey("booking.id"), primary_key=True)
    service_id: Mapped[int] = mapped_column(ForeignKey("service.id"), primary_key=True)

    qty: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    line_sum: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)

    booking: Mapped["Booking"] = relationship(back_populates="services")
    service: Mapped["Service"] = relationship(back_populates="booking_lines")



class Visit(Base):
    __tablename__ = "visit"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    booking_id: Mapped[int] = mapped_column(ForeignKey("booking.id"), nullable=False)
    checkin_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    checkout_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    opened_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("employee.id"), nullable=True)
    closed_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("employee.id"), nullable=True)
    actual_participants_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    booking: Mapped["Booking"] = relationship(back_populates="visit")
    opened_by: Mapped[Optional["Employee"]] = relationship(
        "Employee",
        foreign_keys=[opened_by_id],
        back_populates="opened_visits",
    )
    closed_by: Mapped[Optional["Employee"]] = relationship(
        "Employee",
        foreign_keys=[closed_by_id],
        back_populates="closed_visits",
    )
class Payment(Base):
    __tablename__ = "payment"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    booking_id: Mapped[int] = mapped_column(ForeignKey("booking.id"), nullable=False)
    paid_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    method: Mapped[str] = mapped_column(String, nullable=False)
    comment: Mapped[Optional[str]] = mapped_column(Text)
    created_by_employee_id: Mapped[Optional[int]] = mapped_column(ForeignKey("employee.id"), nullable=True)

    booking: Mapped["Booking"] = relationship(back_populates="payments")


class ClientStatus(Base):
    __tablename__ = "client_status"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)

    clients: Mapped[list["Client"]] = relationship(back_populates="status")


class ScheduleSlot(Base):
    __tablename__ = "schedule_slot"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    zone_id: Mapped[int] = mapped_column(ForeignKey("zone.id"), nullable=False)
    employee_id: Mapped[Optional[int]] = mapped_column(ForeignKey("employee.id"))
    datetime_from: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    datetime_to: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    capacity: Mapped[int] = mapped_column(Integer, nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    lesson_type: Mapped[str] = mapped_column(String, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    zone: Mapped["Zone"] = relationship()
    employee: Mapped[Optional["Employee"]] = relationship()
    bookings: Mapped[list["Booking"]] = relationship(back_populates="schedule_slot")


class SubscriptionStatus(Base):
    __tablename__ = "subscription_status"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)

    subscriptions: Mapped[list["Subscription"]] = relationship(back_populates="status")


class Subscription(Base):
    __tablename__ = "subscription"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("client.id"), nullable=False)
    service_id: Mapped[Optional[int]] = mapped_column(ForeignKey("service.id"))
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    total_visits: Mapped[int] = mapped_column(Integer, nullable=False)
    remaining_visits: Mapped[int] = mapped_column(Integer, nullable=False)
    status_id: Mapped[int] = mapped_column(ForeignKey("subscription_status.id"), nullable=False)

    client: Mapped["Client"] = relationship(back_populates="subscriptions")
    service: Mapped[Optional["Service"]] = relationship()
    status: Mapped["SubscriptionStatus"] = relationship(back_populates="subscriptions")
    bookings: Mapped[list["Booking"]] = relationship(back_populates="subscription")


class Notification(Base):
    __tablename__ = "notification"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("client.id"), nullable=False)
    message: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    is_read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    client: Mapped["Client"] = relationship(back_populates="notifications")
