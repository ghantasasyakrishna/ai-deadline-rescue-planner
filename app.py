from flask import Flask, render_template, request, redirect, url_for, flash
import sqlite3
from datetime import datetime, date, timedelta
from pathlib import Path

app = Flask(__name__)
app.secret_key = "rescue-planner-local-key"

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "tasks.db"
DAILY_HOURS = 5.0


def connect_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def initialize_database():
    with connect_db() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                subject TEXT NOT NULL,
                deadline TEXT NOT NULL,
                hours REAL NOT NULL,
                importance INTEGER NOT NULL DEFAULT 3,
                completed INTEGER NOT NULL DEFAULT 0
            )
        """)


def get_priority(task):
    due = datetime.fromisoformat(task["deadline"])
    now = datetime.now()

    if due < now:
        urgency = 0
    elif due <= now + timedelta(hours=24):
        urgency = 1
    elif due <= now + timedelta(hours=72):
        urgency = 2
    else:
        urgency = 3

    return urgency, due, -task["importance"]


def prepare_tasks(tasks):
    now = datetime.now()
    today = date.today()

    daily_capacity = {}
    today_plan = []
    future_plan = []
    reschedule = []
    completed = []
    pending = []

    # 1. Separate completed and unfinished tasks first.
    for task in tasks:
        due = datetime.fromisoformat(task["deadline"])
        task["deadline_display"] = due.strftime("%d %b %Y, %I:%M %p")

        if task["completed"]:
            task["status"] = "Completed"
            task["status_class"] = "completed"
            task["plan_status"] = "Completed"
            completed.append(task)
            continue

        if due < now:
            task["status"] = "Overdue"
            task["status_class"] = "urgent"
        elif due <= now + timedelta(hours=24):
            task["status"] = "Urgent"
            task["status_class"] = "urgent"
        elif due <= now + timedelta(hours=72):
            task["status"] = "Upcoming"
            task["status_class"] = "upcoming"
        else:
            task["status"] = "Planned"
            task["status_class"] = "planned"

        pending.append(task)

    # 2. Schedule only unfinished tasks.
    pending.sort(key=get_priority)

    for task in pending:
        due = datetime.fromisoformat(task["deadline"])

        # Overdue tasks belong only in rescheduling.
        if due < now:
            task["plan_status"] = "Needs rescheduling"
            reschedule.append(task)
            continue

        # Tasks longer than the daily limit cannot fit in one day.
        if task["hours"] > DAILY_HOURS:
            task["plan_status"] = "Needs rescheduling"
            reschedule.append(task)
            continue

        # Find the earliest day with enough available hours.
        assigned_day = None
        day = today

        while day <= due.date():
            used = daily_capacity.get(day, 0.0)

            if used + task["hours"] <= DAILY_HOURS:
                daily_capacity[day] = used + task["hours"]
                assigned_day = day
                break

            day += timedelta(days=1)

        if assigned_day is None:
            task["plan_status"] = "Needs rescheduling"
            reschedule.append(task)

        elif assigned_day == today:
            task["plan_status"] = "Scheduled today"
            today_plan.append(task)

        else:
            task["plan_status"] = (
                "Scheduled: " + assigned_day.strftime("%d %b")
            )
            task["scheduled_date"] = assigned_day.strftime("%d %b %Y")
            future_plan.append(task)

    # 3. Sort each section independently.
    today_plan.sort(key=get_priority)
    future_plan.sort(
        key=lambda task: (
            task["scheduled_date"],
            get_priority(task)
        )
    )
    reschedule.sort(key=get_priority)
    completed.sort(key=get_priority)

    # 4. All tasks: unfinished first, completed last.
    tasks.sort(
        key=lambda task: (
            bool(task["completed"]),
            get_priority(task)
        )
    )

    return today_plan, future_plan, reschedule, completed, daily_capacity


@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        action = request.form.get("action", "")

        if action == "add":
            title = request.form.get("title", "").strip()
            subject = request.form.get("subject", "").strip()
            deadline_text = request.form.get("deadline", "").strip()

            try:
                hours = float(request.form.get("hours", "0"))
                importance = int(request.form.get("importance", "3"))
                deadline = datetime.fromisoformat(deadline_text)

                if not title or not subject:
                    raise ValueError("Title and subject are required.")

                if hours <= 0 or hours > 1000:
                    raise ValueError("Enter valid study hours.")

                if importance not in range(1, 6):
                    raise ValueError("Importance must be between 1 and 5.")

                with connect_db() as db:
                    db.execute("""
                        INSERT INTO tasks
                        (title, subject, deadline, hours, importance)
                        VALUES (?, ?, ?, ?, ?)
                    """, (
                        title,
                        subject,
                        deadline.isoformat(timespec="minutes"),
                        hours,
                        importance
                    ))

                flash("Task added successfully!", "success")

            except (ValueError, TypeError):
                flash(
                    "Invalid details. Check the title, subject, deadline, "
                    "study hours and importance.",
                    "error"
                )

        elif action == "toggle":
            try:
                task_id = int(request.form.get("task_id", "0"))

                with connect_db() as db:
                    db.execute("""
                        UPDATE tasks
                        SET completed = CASE
                            WHEN completed = 0 THEN 1
                            ELSE 0
                        END
                        WHERE id = ?
                    """, (task_id,))

                flash("Task status updated.", "success")

            except ValueError:
                flash("Invalid task.", "error")

        elif action == "delete":
            try:
                task_id = int(request.form.get("task_id", "0"))

                with connect_db() as db:
                    db.execute(
                        "DELETE FROM tasks WHERE id = ?",
                        (task_id,)
                    )

                flash("Task deleted.", "success")

            except ValueError:
                flash("Invalid task.", "error")

        return redirect(url_for("index"))

    with connect_db() as db:
        tasks = [
            dict(row)
            for row in db.execute("SELECT * FROM tasks").fetchall()
        ]

    plan, future_plan, reschedule, completed, capacity = prepare_tasks(tasks)

    total = len(tasks)
    completed_count = len(completed)
    pending = [task for task in tasks if not task["completed"]]

    progress = round(completed_count * 100 / total) if total else 0
    today_used = round(capacity.get(date.today(), 0.0), 1)

    return render_template(
        "index.html",
        tasks=tasks,
        plan=plan,
        future_plan=future_plan,
        reschedule=reschedule,
        completed=completed,
        total=total,
        completed_count=completed_count,
        pending_count=len(pending),
        remaining_hours=round(
            sum(task["hours"] for task in pending), 1
        ),
        available_hours=DAILY_HOURS,
        today_used=today_used,
        today_remaining=round(DAILY_HOURS - today_used, 1),
        progress=progress,
        today_display=date.today().strftime("%A, %d %B %Y")
    )


initialize_database()

if __name__ == "__main__":
    app.run(debug=True)