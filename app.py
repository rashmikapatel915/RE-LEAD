from flask import Flask, render_template, request, redirect
import sqlite3
import os
from datetime import datetime
from hindsight_client import Hindsight


app = Flask(
    __name__,
    static_folder="static",
    static_url_path="/static",
    template_folder="templates"
)

DATABASE = "database.db"


# =========================================================
# HINDSIGHT CONFIGURATION
# =========================================================

HINDSIGHT_API_KEY = os.environ.get("HINDSIGHT_API_KEY")

hindsight = None

if HINDSIGHT_API_KEY:
    hindsight = Hindsight(
        base_url="https://api.hindsight.vectorize.io",
        api_key=HINDSIGHT_API_KEY
    )

BANK_ID = "relead"


# =========================================================
# DATABASE
# =========================================================

def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():

    conn = get_db()

    # -----------------------------------------------------
    # CUSTOMERS TABLE
    # -----------------------------------------------------

    conn.execute("""
        CREATE TABLE IF NOT EXISTS customers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            phone TEXT NOT NULL,
            service TEXT,
            reason TEXT,
            priority TEXT DEFAULT 'Medium',
            status TEXT DEFAULT 'Lost'
        )
    """)

    # -----------------------------------------------------
    # ADD RE:LEAD FIELDS IF THEY DON'T EXIST
    # -----------------------------------------------------

    columns = [
        ("company", "TEXT"),
        ("requirement", "TEXT"),
        ("budget", "TEXT"),
        ("objection", "TEXT"),
        ("notes", "TEXT"),
        ("outcome", "TEXT")
    ]

    existing_columns = [
        row["name"]
        for row in conn.execute(
            "PRAGMA table_info(customers)"
        ).fetchall()
    ]

    for column_name, column_type in columns:

        if column_name not in existing_columns:

            conn.execute(
                f"ALTER TABLE customers ADD COLUMN {column_name} {column_type}"
            )

    # -----------------------------------------------------
    # LEAD HISTORY TABLE
    # -----------------------------------------------------

    conn.execute("""
        CREATE TABLE IF NOT EXISTS lead_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER NOT NULL,
            interaction_type TEXT DEFAULT 'Interaction',
            message TEXT,
            outcome TEXT,
            status TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (customer_id) REFERENCES customers(id)
        )
    """)

    conn.commit()
    conn.close()


# =========================================================
# HINDSIGHT SETUP
# =========================================================

def setup_hindsight():

    if not hindsight:

        print("WARNING: HINDSIGHT_API_KEY not found.")
        print("RE:LEAD will run without Hindsight memory.")

        return

    try:

        hindsight.create_bank(
            bank_id=BANK_ID,
            name="RE:LEAD Sales Memory Agent"
        )

        print("Hindsight bank created.")

    except Exception as e:

        print("Hindsight bank:", e)


# =========================================================
# HOME / DASHBOARD
# =========================================================

@app.route("/")
def home():

    conn = get_db()

    customers = conn.execute("""
        SELECT *
        FROM customers
        ORDER BY id DESC
    """).fetchall()

    total = conn.execute(
        "SELECT COUNT(*) FROM customers"
    ).fetchone()[0]

    lost = conn.execute(
        "SELECT COUNT(*) FROM customers WHERE status='Lost'"
    ).fetchone()[0]

    followups = conn.execute(
        "SELECT COUNT(*) FROM customers WHERE status='Follow Up'"
    ).fetchone()[0]

    recovered = conn.execute(
        "SELECT COUNT(*) FROM customers WHERE status='Recovered'"
    ).fetchone()[0]

    conn.close()

    return render_template(
        "index.html",
        customers=customers,
        total=total,
        lost=lost,
        followups=followups,
        recovered=recovered
    )


# =========================================================
# LEAD DETAILS + COMPLETE HISTORY
# =========================================================

@app.route("/lead/<int:customer_id>")
def lead_details(customer_id):

    conn = get_db()

    customer = conn.execute(
        "SELECT * FROM customers WHERE id=?",
        (customer_id,)
    ).fetchone()

    if customer is None:

        conn.close()
        return redirect("/")

    # Get complete local history
    history = conn.execute("""
        SELECT *
        FROM lead_history
        WHERE customer_id=?
        ORDER BY id ASC
    """, (customer_id,)).fetchall()

    conn.close()

    return render_template(
        "lead.html",
        customer=customer,
        history=history,
        answer=None,
        question=None,
        memories=[]
    )


# =========================================================
# ADD LEAD
# =========================================================

@app.route("/add", methods=["POST"])
def add_customer():

    name = request.form.get("name", "")
    phone = request.form.get("phone", "")

    company = request.form.get("company", "")
    service = request.form.get("service", "")
    requirement = request.form.get("requirement", "")
    budget = request.form.get("budget", "")

    reason = request.form.get("reason", "")
    objection = request.form.get("objection", "")

    priority = request.form.get("priority", "Medium")
    notes = request.form.get("notes", "")

    conn = get_db()

    cursor = conn.execute("""
        INSERT INTO customers
        (
            name,
            phone,
            company,
            service,
            requirement,
            budget,
            reason,
            objection,
            priority,
            notes
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        name,
        phone,
        company,
        service,
        requirement,
        budget,
        reason,
        objection,
        priority,
        notes
    ))

    customer_id = cursor.lastrowid

    # -----------------------------------------------------
    # CREATE FIRST HISTORY ENTRY
    # -----------------------------------------------------

    history_message = f"""
Lead created.

Company: {company}
Service: {service}
Requirement: {requirement}
Budget: {budget}
Reason: {reason}
Objection: {objection}
Priority: {priority}
Notes: {notes}
"""

    conn.execute("""
        INSERT INTO lead_history
        (
            customer_id,
            interaction_type,
            message,
            outcome,
            status,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        customer_id,
        "Lead Created",
        history_message,
        "",
        "Lost",
        datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ))

    conn.commit()
    conn.close()

    # -----------------------------------------------------
    # STORE LEAD IN HINDSIGHT
    # -----------------------------------------------------

    if hindsight:

        try:

            memory = f"""
RE:LEAD SALES HISTORY

Lead ID: {customer_id}

Name: {name}
Company: {company}
Service: {service}
Requirement: {requirement}
Budget: {budget}

Reason for stalled/lost status:
{reason}

Specific customer objection:
{objection}

Priority:
{priority}

Sales notes:
{notes}

This is a historical sales interaction.

Use this experience when recommending strategies for
future leads with similar requirements, budgets,
objections, or buying behavior.
"""

            hindsight.retain(
                bank_id=BANK_ID,
                content=memory,
                document_id=f"lead-{customer_id}-created"
            )

            print(
                f"Hindsight memory stored for lead #{customer_id}"
            )

        except Exception as e:

            print(
                "Hindsight retain error:",
                e
            )

    return redirect("/")


# =========================================================
# ADD CUSTOMER INTERACTION / HISTORY
# =========================================================

@app.route("/lead/<int:customer_id>/interaction", methods=["POST"])
def add_interaction(customer_id):

    interaction_type = request.form.get(
        "interaction_type",
        "Interaction"
    )

    message = request.form.get(
        "message",
        ""
    )

    outcome = request.form.get(
        "outcome",
        ""
    )

    if not message.strip():

        return redirect(f"/lead/{customer_id}")

    conn = get_db()

    customer = conn.execute(
        "SELECT * FROM customers WHERE id=?",
        (customer_id,)
    ).fetchone()

    if customer is None:

        conn.close()
        return redirect("/")

    created_at = datetime.now().strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    # Save local history
    cursor = conn.execute("""
        INSERT INTO lead_history
        (
            customer_id,
            interaction_type,
            message,
            outcome,
            status,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        customer_id,
        interaction_type,
        message,
        outcome,
        customer["status"],
        created_at
    ))

    history_id = cursor.lastrowid

    conn.commit()
    conn.close()

    # -----------------------------------------------------
    # STORE INTERACTION IN HINDSIGHT
    # -----------------------------------------------------

    if hindsight:

        try:

            memory = f"""
RE:LEAD CUSTOMER INTERACTION

Lead ID: {customer_id}
Customer: {customer['name']}
Company: {customer['company']}

Interaction type:
{interaction_type}

Date:
{created_at}

Customer / salesperson interaction:
{message}

Outcome:
{outcome}

Current lead status:
{customer['status']}

This interaction is part of the organization's
sales history.

Learn from this interaction when analyzing future
leads with similar objections, requirements,
companies, budgets, or buying behavior.
"""

            hindsight.retain(
                bank_id=BANK_ID,
                content=memory,
                document_id=f"lead-{customer_id}-interaction-{history_id}"
            )

            print(
                f"Hindsight interaction stored for lead #{customer_id}"
            )

        except Exception as e:

            print(
                "Hindsight interaction error:",
                e
            )

    return redirect(f"/lead/{customer_id}")


# =========================================================
# ASK RE:LEAD
# =========================================================

@app.route("/ask/<int:customer_id>", methods=["POST"])
def ask_relead(customer_id):

    question = request.form.get(
        "question",
        "What should we do with this lead?"
    )

    conn = get_db()

    customer = conn.execute(
        "SELECT * FROM customers WHERE id=?",
        (customer_id,)
    ).fetchone()

    history = conn.execute("""
        SELECT *
        FROM lead_history
        WHERE customer_id=?
        ORDER BY id ASC
    """, (customer_id,)).fetchall()

    conn.close()

    if customer is None:

        return redirect("/")

    if not hindsight:

        return render_template(
            "lead.html",
            customer=customer,
            history=history,
            answer="Hindsight is not connected. Please check your API key.",
            question=question,
            memories=[]
        )

    try:

        # =================================================
        # RECALL RELEVANT SALES MEMORIES
        # =================================================

        recall_result = hindsight.recall(
            bank_id=BANK_ID,
            query=f"""
Find previous sales experiences similar to this lead.

Current lead:

Lead ID: {customer_id}
Company: {customer['company']}
Service: {customer['service']}
Requirement: {customer['requirement']}
Budget: {customer['budget']}
Reason: {customer['reason']}
Objection: {customer['objection']}

Question:
{question}

Look for previous experiences involving:
- similar objections
- similar requirements
- similar budgets
- similar buying behavior
- successful outcomes
- unsuccessful outcomes
""",
           
        )

        memories = []

        for memory in recall_result.results:

            memories.append(memory.text)

        # =================================================
        # REFLECT OVER MEMORY
        # =================================================

        history_text = ""

        for item in history:

            history_text += f"""
Date: {item['created_at']}
Type: {item['interaction_type']}
Interaction: {item['message']}
Outcome: {item['outcome']}
Status: {item['status']}
---
"""

      
You are RE:LEAD, an organizational sales memory agent.

Your job is to help a sales team learn from previous
customer interactions.

CURRENT LEAD

Name: {customer['name']}
Company: {customer['company']}
Service: {customer['service']}
Requirement: {customer['requirement']}
Budget: {customer['budget']}
Reason: {customer['reason']}
Objection: {customer['objection']}
Notes: {customer['notes']}

CURRENT CUSTOMER HISTORY

{history_text}

USER QUESTION

{question}

Use relevant historical sales experiences from memory.

Compare what happened in similar past cases.

Identify patterns in what worked and what failed.

Then provide:

1. Recommended next action
2. Why this action is supported by past experiences
3. A short follow-up message the salesperson can send
4. What outcome should be recorded after the interaction

Do not guarantee that the recommendation will convert
the lead.

Focus on learning from organizational sales history.
"""
# =================================================
# BUILD RECOMMENDATION FROM HINDSIGHT MEMORIES
# =================================================

if memories:

    memory_text = "\n\n".join(
        f"Past sales experience {i + 1}:\n{memory}"
        for i, memory in enumerate(memories)
    )

  answer = f"""
RE:LEAD RECOMMENDATION


{memory_text}

Recommended next action:
Review the successful patterns from these past sales experiences and use the approach that best matches the customer's current situation.

RE:LEAD is using Hindsight memory to avoid repeating past sales mistakes.
"""

Suggested follow-up:
"I understand your concern. Based on what you're
looking for, I can suggest an option that better
matches your requirements. Would you like me to
share the details?"

Next step:
Record the customer's response as a new interaction
so RE:LEAD can learn from the outcome.

Note:
This is a recommendation based on historical memory,
not a guarantee of conversion.
"""

else:

    answer = """
RE:LEAD could not find a sufficiently relevant
past sales experience.

Recommended next action:
Record the customer's current interaction and outcome.
This will give RE:LEAD new organizational memory to
learn from for future leads.
"""

        return render_template(
            "lead.html",
            customer=customer,
            history=history,
            answer=answer,
            memories=memories,
            question=question
        )

    except Exception as e:

        print(
            "RE:LEAD AI error:",
            e
        )

        return render_template(
            "lead.html",
            customer=customer,
            history=history,
            answer=(
                "RE:LEAD could not generate the recommendation "
                "right now. Error: " + str(e)
            ),
            memories=[],
            question=question
        )


# =========================================================
# UPDATE STATUS
# =========================================================

@app.route("/status/<int:customer_id>/<status>")
def update_status(customer_id, status):

    allowed = [
        "Lost",
        "Follow Up",
        "Recovered"
    ]

    if status not in allowed:

        return redirect("/")

    conn = get_db()

    customer = conn.execute(
        "SELECT * FROM customers WHERE id=?",
        (customer_id,)
    ).fetchone()

    if customer is None:

        conn.close()
        return redirect("/")

    # Update customer
    conn.execute("""
        UPDATE customers
        SET status=?
        WHERE id=?
    """, (
        status,
        customer_id
    ))

    created_at = datetime.now().strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    # Add status change to history
    conn.execute("""
        INSERT INTO lead_history
        (
            customer_id,
            interaction_type,
            message,
            outcome,
            status,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        customer_id,
        "Status Update",
        f"Lead status changed from {customer['status']} to {status}.",
        status,
        status,
        created_at
    ))

    conn.commit()
    conn.close()

    # -----------------------------------------------------
    # REMEMBER OUTCOME IN HINDSIGHT
    # -----------------------------------------------------

    if hindsight:

        try:

            hindsight.retain(
                bank_id=BANK_ID,
                content=f"""
RE:LEAD SALES OUTCOME

Lead ID: {customer_id}
Customer: {customer['name']}
Company: {customer['company']}

Previous status:
{customer['status']}

New status:
{status}

This is a sales outcome.

Use this outcome when learning from future sales
interactions involving similar customers,
requirements, objections, budgets, or situations.
""",
                document_id=f"lead-{customer_id}-outcome-{datetime.now().timestamp()}"
            )

            print(
                f"Hindsight outcome stored for lead #{customer_id}"
            )

        except Exception as e:

            print(
                "Hindsight outcome error:",
                e
            )

    return redirect("/")


# =========================================================
# DELETE LEAD
# =========================================================

@app.route("/delete/<int:customer_id>")
def delete_customer(customer_id):

    conn = get_db()

    conn.execute(
        "DELETE FROM lead_history WHERE customer_id=?",
        (customer_id,)
    )

    conn.execute(
        "DELETE FROM customers WHERE id=?",
        (customer_id,)
    )

    conn.commit()
    conn.close()

    return redirect("/")


# =========================================================
# RUN APPLICATION
# =========================================================

if __name__ == "__main__":

    init_db()
    setup_hindsight()

    print("")
    print("========================================")
    print("       RE:LEAD SALES MEMORY AGENT")
    print("========================================")

    if hindsight:
        print("Hindsight: CONNECTED")
    else:
        print("Hindsight: NOT CONNECTED")

    print("")

    app.run(
        debug=True
    )