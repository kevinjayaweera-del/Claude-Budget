def categorize(description, conn):
    text = description.lower()
    rules = conn.execute(
        "SELECT keyword, category_id FROM category_rules ORDER BY LENGTH(keyword) DESC"
    ).fetchall()
    for rule in rules:
        if rule["keyword"] in text:
            return rule["category_id"]
    return _get_category_id(conn, "Unkategorisiert")


def learn_rule(conn, description, category_id):
    keyword = _extract_keyword(description)
    if not keyword:
        return
    conn.execute(
        "INSERT OR REPLACE INTO category_rules (keyword, category_id) VALUES (?, ?)",
        (keyword, category_id),
    )
    conn.commit()


def _get_category_id(conn, name):
    row = conn.execute("SELECT id FROM categories WHERE name = ?", (name,)).fetchone()
    return row["id"] if row else None


def _extract_keyword(description):
    words = [w for w in description.split() if len(w) > 3]
    candidate = words[0] if words else description.strip()
    return candidate.lower()
