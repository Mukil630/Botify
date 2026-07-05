"""
One-time DB migration: add new columns to existing tables.
Run once: python migrate_db.py
"""
import sqlite3, os

DB_PATH = os.path.join(os.path.dirname(__file__), 'instance', 'saas.db')
if not os.path.exists(DB_PATH):
    # Try root-level saas.db
    DB_PATH = os.path.join(os.path.dirname(__file__), 'saas.db')

print(f'Using DB: {DB_PATH}')

conn   = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

def add_column_if_missing(table, column, col_type, default=None):
    cursor.execute(f"PRAGMA table_info({table})")
    cols = [row[1] for row in cursor.fetchall()]
    if column not in cols:
        if default is not None:
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type} DEFAULT {default}")
        else:
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
        print(f'  + Added {table}.{column}')
    else:
        print(f'  - {table}.{column} already exists')

print('\n--- Migrating service table ---')
add_column_if_missing('service', 'category',     'VARCHAR(50)',  "'General'")
add_column_if_missing('service', 'image_url',    'VARCHAR(300)', "''")
add_column_if_missing('service', 'is_available', 'BOOLEAN',      '1')
add_column_if_missing('service', 'sort_order',   'INTEGER',      '0')

print('\n--- Migrating booking table ---')
add_column_if_missing('booking', 'reminder_sent',  'BOOLEAN', '0')
add_column_if_missing('booking', 'payment_status', 'VARCHAR(20)', "'unpaid'")
add_column_if_missing('booking', 'notes',          'TEXT')

print('\n--- Migrating bot table ---')
add_column_if_missing('bot', 'personality', 'TEXT')
add_column_if_missing('bot', 'language',    'VARCHAR(10)', "'auto'")

print('\n--- Migrating business_info table ---')
add_column_if_missing('business_info', 'contact_phone', 'VARCHAR(20)')
add_column_if_missing('business_info', 'contact_email', 'VARCHAR(100)')
add_column_if_missing('business_info', 'gst_rate',      'FLOAT',        '18.0')
add_column_if_missing('business_info', 'gst_number',    'VARCHAR(50)')

print('\n--- Creating new tables (if missing) ---')
cursor.execute("""
CREATE TABLE IF NOT EXISTS "order" (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES user(id),
    customer_phone VARCHAR(20),
    customer_name VARCHAR(100),
    items TEXT,
    total_amount FLOAT DEFAULT 0,
    status VARCHAR(20) DEFAULT 'pending',
    payment_status VARCHAR(20) DEFAULT 'unpaid',
    notes TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
)
""")
print('  + order table OK')

cursor.execute("""
CREATE TABLE IF NOT EXISTS broadcast_log (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES user(id),
    message TEXT NOT NULL,
    recipient_count INTEGER DEFAULT 0,
    status VARCHAR(20) DEFAULT 'sent',
    sent_at DATETIME DEFAULT CURRENT_TIMESTAMP
)
""")
print('  + broadcast_log table OK')

cursor.execute("""
CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES user(id),
    booking_id INTEGER REFERENCES booking(id),
    customer_phone VARCHAR(20),
    customer_name VARCHAR(100),
    rating INTEGER,
    comment TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
)
""")
print('  + feedback table OK')

conn.commit()
conn.close()
print('\nMigration complete!')
