import streamlit as st
import sqlite3
import pandas as pd
import time
import matplotlib.pyplot as plt

# -------------------- DATABASE CONNECTION --------------------
DB_PATH = r"C:\database\foodappdb.db"

def get_connection():
    return sqlite3.connect(DB_PATH)

# -------------------- SCHEMA DISCOVERY HELPERS --------------------
def table_columns(conn, table):
    """Return {lower_name: actual_name} for a table (empty dict if table missing)."""
    try:
        rows = pd.read_sql(f"PRAGMA table_info({table});", conn)
        return {str(n).lower(): n for n in rows['name'].tolist()}
    except Exception:
        return {}

def pick(cols_map, candidates):
    """Pick first candidate present. Returns actual column name or None."""
    for c in candidates:
        if c.lower() in cols_map:
            return cols_map[c.lower()]
    return None

def coalesce_expr(cols_map, table_alias, candidates):
    """Build COALESCE(expr...) using available columns from candidates."""
    present = [f"{table_alias}.{cols_map[c.lower()]}" for c in candidates if c.lower() in cols_map]
    if not present:
        return None
    if len(present) == 1:
        return present[0]
    return "COALESCE(" + ", ".join(present) + ")"

def build_schema(conn):
    P = table_columns(conn, "providers")
    R = table_columns(conn, "receivers")
    F = table_columns(conn, "food_listings")
    C = table_columns(conn, "claims")

    schema = {
        # providers
        "p_id":           pick(P, ["provider_id","prov_id","id"]),
        "p_name":         pick(P, ["name","provider_name"]),
        "p_type":         pick(P, ["provider_type","type","category"]),
        "p_city":         pick(P, ["city","location","city_name"]),
        "p_contact_one":  pick(P, ["contact_info","contact","phone","email","contact_number","mobile","contact_no"]),
        # receivers
        "r_id":           pick(R, ["receiver_id","recv_id","id"]),
        "r_name":         pick(R, ["name","receiver_name"]),
        "r_city":         pick(R, ["city","location","city_name"]),
        # food_listings
        "f_id":           pick(F, ["listing_id","id","food_id","listingid"]),
        "f_provider_id":  pick(F, ["provider_id","prov_id","providerid","p_id"]),
        "f_city":         pick(F, ["city","location","city_name"]),
        "f_food_type":    pick(F, ["food_type","type","category","meal_type","Food_Type"]),
        "f_quantity":     pick(F, ["quantity","qty","amount"]),
        "f_date_posted":  pick(F, ["date_posted","posted_date","created_at","date"]),
        "f_expiry":       pick(F, ["expiry_date","expires_on","expiry","exp_date"]),
        "f_item":         pick(F, ["food_item","item","item_name","food_name","title"]),
        # claims
        "c_id":               pick(C, ["claim_id","id"]),
        "c_receiver_id":      pick(C, ["receiver_id","recv_id","receiverid","r_id"]),
        "c_listing_id":       pick(C, ["listing_id","food_listing_id","food_id","listingid"]),
        "c_qty_claimed":      pick(C, ["quantity_claimed","quantity","qty","amount"]),
        "c_status":           pick(C, ["status","claim_status","state"]),
        "c_meal_type":        pick(C, ["meal_type","food_type","type"]),
    }

    # contact expression (use whichever exists)
    schema["p_contact_expr"] = coalesce_expr(P, "p", ["contact_info","contact","phone","email","contact_number","mobile","contact_no"])

    schema["_tables"] = {"P": P, "R": R, "F": F, "C": C}
    return schema

# -------------------- QUERY FACTORY (builds only valid queries) --------------------
def build_queries(s):
    Q = {}

    # 1. Providers & Receivers per City  (needs p_city; receivers join optional if r_city exists)
    if s["p_city"]:
        if s["r_city"]:
            Q["1. Providers & Receivers per City"] = f"""
                SELECT p.{s['p_city']} AS city,
                       COUNT(DISTINCT p.{s['p_id']}) AS total_providers,
                       COUNT(DISTINCT r.{s['r_id']}) AS total_receivers
                FROM providers p
                LEFT JOIN receivers r ON p.{s['p_city']} = r.{s['r_city']}
                GROUP BY p.{s['p_city']};
            """
        else:
            Q["1. Providers & Receivers per City"] = f"""
                SELECT p.{s['p_city']} AS city,
                       COUNT(DISTINCT p.{s['p_id']}) AS total_providers,
                       0 AS total_receivers
                FROM providers p
                GROUP BY p.{s['p_city']};
            """

    # 2. Provider Type Contribution (needs p_type)
    if s["p_type"]:
        Q["2. Provider Type Contribution"] = f"""
            SELECT p.{s['p_type']} AS provider_type,
                   COUNT(p.{s['p_id']}) AS total_providers
            FROM providers p
            GROUP BY p.{s['p_type']}
            ORDER BY total_providers DESC;
        """

    # 3. Contact Info by City (needs p_name + p_city; contact optional)
    if s["p_name"] and s["p_city"]:
        contact_sel = s["p_contact_expr"] or f"p.{s['p_name']}"  # fallback: repeat name
        Q["3. Contact Info by City"] = f"""
            -- city param will be injected dynamically from UI
            SELECT p.{s['p_name']} AS name,
                   {contact_sel} AS contact_info,
                   p.{s['p_city']} AS city
            FROM providers p
            WHERE p.{s['p_city']} = ?;
        """

    # 4. Top Receivers by Claims (needs r_id + c_id + c_receiver_id)
    if s["r_name"] and s["r_id"] and s["c_id"] and s["c_receiver_id"]:
        Q["4. Top Receivers by Claims"] = f"""
            SELECT r.{s['r_name']} AS name,
                   COUNT(c.{s['c_id']}) AS total_claims
            FROM receivers r
            JOIN claims c ON r.{s['r_id']} = c.{s['c_receiver_id']}
            GROUP BY r.{s['r_name']}
            ORDER BY total_claims DESC
            LIMIT 10;
        """

    # 5. Total Food Available (needs f_quantity)
    if s["f_quantity"]:
        Q["5. Total Food Available"] = f"SELECT SUM({s['f_quantity']}) AS total_quantity FROM food_listings;"

    # 6. City with Highest Listings (if listings lack city, derive via providers)
    if s["f_id"]:
        if s["f_city"]:
            Q["6. City with Highest Listings"] = f"""
                SELECT f.{s['f_city']} AS city,
                       COUNT(f.{s['f_id']}) AS total_listings
                FROM food_listings f
                GROUP BY f.{s['f_city']}
                ORDER BY total_listings DESC
                LIMIT 1;
            """
        elif s["f_provider_id"] and s["p_city"]:
            Q["6. City with Highest Listings"] = f"""
                SELECT p.{s['p_city']} AS city,
                       COUNT(f.{s['f_id']}) AS total_listings
                FROM food_listings f
                JOIN providers p ON f.{s['f_provider_id']} = p.{s['p_id']}
                GROUP BY p.{s['p_city']}
                ORDER BY total_listings DESC
                LIMIT 1;
            """

    # 7. Most Common Food Types (needs food type; either on listings or claims)
    if s["f_food_type"]:
        Q["7. Most Common Food Types"] = f"""
            SELECT f.{s['f_food_type']} AS Food_Type,
                   COUNT(*) AS frequency
            FROM food_listings f
            GROUP BY f.{s['f_food_type']}
            ORDER BY frequency DESC
            LIMIT 5;
        """

    # 8. Claims per Food Item (needs join claims->listings via a key + food_type)
    if s["c_id"] and s["f_food_type"] and s["c_listing_id"] and s["f_id"]:
        Q["8. Claims per Food Item"] = f"""
            SELECT f.{s['f_food_type']} AS food_type,
                   COUNT(c.{s['c_id']}) AS total_claims
            FROM claims c
            JOIN food_listings f ON c.{s['c_listing_id']} = f.{s['f_id']}
            GROUP BY f.{s['f_food_type']}
            ORDER BY total_claims DESC;
        """

    # 9. Provider with Most Claims (needs listing id, c_listing_id, p_id/name)
    if s["p_name"] and s["f_id"] and s["f_provider_id"] and s["c_id"] and s["c_listing_id"]:
        Q["9. Provider with Most Claims"] = f"""
            SELECT p.{s['p_name']} AS name,
                   COUNT(c.{s['c_id']}) AS total_claims
            FROM providers p
            JOIN food_listings f ON p.{s['p_id']} = f.{s['f_provider_id']}
            JOIN claims c ON f.{s['f_id']} = c.{s['c_listing_id']}
            GROUP BY p.{s['p_name']}
            ORDER BY total_claims DESC
            LIMIT 1;
        """

    # 10. Claim Status Percentages (needs c_status)
    if s["c_status"]:
        Q["10. Claim Status Percentages"] = f"""
            SELECT {s['c_status']} AS status,
                   ROUND((COUNT(*) * 100.0) / (SELECT COUNT(*) FROM claims), 2) AS percentage
            FROM claims
            GROUP BY {s['c_status']};
        """

    # 11. Avg Quantity Claimed per Receiver (needs qty_claimed + receiver join)
    if s["c_qty_claimed"] and s["r_id"] and s["r_name"] and s["c_receiver_id"]:
        Q["11. Avg Quantity Claimed per Receiver"] = f"""
            SELECT r.{s['r_name']} AS name,
                   AVG(c.{s['c_qty_claimed']}) AS avg_quantity
            FROM claims c
            JOIN receivers r ON c.{s['c_receiver_id']} = r.{s['r_id']}
            GROUP BY r.{s['r_name']}
            ORDER BY avg_quantity DESC
            LIMIT 10;
        """

    # 12. Most Claimed Meal Type (prefer claims.meal_type, else join to listings.food_type)
    if s["c_meal_type"]:
        Q["12. Most Claimed Meal Type"] = f"""
            SELECT {s['c_meal_type']} AS meal_type,
                   COUNT(*) AS total_claims
            FROM claims
            GROUP BY {s['c_meal_type']}
            ORDER BY total_claims DESC
            LIMIT 1;
        """
    elif s["f_food_type"] and s["c_listing_id"] and s["f_id"]:
        Q["12. Most Claimed Meal Type"] = f"""
            SELECT f.{s['f_food_type']} AS meal_type,
                   COUNT(*) AS total_claims
            FROM claims c
            JOIN food_listings f ON c.{s['c_listing_id']} = f.{s['f_id']}
            GROUP BY f.{s['f_food_type']}
            ORDER BY total_claims DESC
            LIMIT 1;
        """

    # 13. Total Food Donated per Provider (needs p_name + f_quantity + f_provider_id)
    if s["p_name"] and s["f_quantity"] and s["f_provider_id"]:
        Q["13. Total Food Donated per Provider"] = f"""
            SELECT p.{s['p_name']} AS name,
                   SUM(f.{s['f_quantity']}) AS total_donated
            FROM providers p
            JOIN food_listings f ON p.{s['p_id']} = f.{s['f_provider_id']}
            GROUP BY p.{s['p_name']}
            ORDER BY total_donated DESC;
        """

    # 14. City with Most Successful Claims (needs c_status + r_city)
    if s["c_status"] and s["r_city"] and s["c_receiver_id"] and s["r_id"]:
        Q["14. City with Most Successful Claims"] = f"""
            SELECT r.{s['r_city']} AS city,
                   COUNT(c.{s['c_id']}) AS successful_claims
            FROM claims c
            JOIN receivers r ON c.{s['c_receiver_id']} = r.{s['r_id']}
            WHERE c.{s['c_status']} = 'Completed'
            GROUP BY r.{s['r_city']}
            ORDER BY successful_claims DESC
            LIMIT 1;
        """

    # 15. Closest to Expiry but Available (needs f_expiry and optionally f_item)
    if s["f_expiry"]:
        item_sel = s["f_item"] or s["f_id"]
        Q["15. Closest to Expiry but Available"] = f"""
            SELECT {item_sel} AS food_item, {s['f_expiry']} AS expiry_date
            FROM food_listings
            WHERE DATE({s['f_expiry']}) > DATE('now')
            ORDER BY DATE({s['f_expiry']}) ASC
            LIMIT 1;
        """

    return Q

# -------------------- STREAMLIT APP --------------------
st.set_page_config(page_title="Food App Dashboard", layout="wide")
st.title("🍽️ Food Donation Dashboard")

menu = st.sidebar.radio("Navigate", ["KPIs", "SQL Insights", "CRUD Operations", "EDA Visualizations"])

# -------------------- KPIs --------------------
if menu == "KPIs":
    st.header("📊 Key Performance Indicators")
    conn = get_connection()
    try:
        total_providers = pd.read_sql("SELECT COUNT(*) AS n FROM providers;", conn)["n"].iat[0]
    except Exception:
        total_providers = 0
    try:
        total_receivers = pd.read_sql("SELECT COUNT(*) AS n FROM receivers;", conn)["n"].iat[0]
    except Exception:
        total_receivers = 0
    try:
        total_listings = pd.read_sql("SELECT COUNT(*) AS n FROM food_listings;", conn)["n"].iat[0]
    except Exception:
        total_listings = 0
    try:
        total_claims = pd.read_sql("SELECT COUNT(*) AS n FROM claims;", conn)["n"].iat[0]
    except Exception:
        total_claims = 0
    conn.close()

    kpi1, kpi2, kpi3, kpi4 = st.columns(4)
    kpi1.metric("Total Providers", int(total_providers))
    kpi2.metric("Total Receivers", int(total_receivers))
    kpi3.metric("Total Listings", int(total_listings))
    kpi4.metric("Total Claims", int(total_claims))

# 📈 Visualization after KPIs
    st.subheader("📊 Providers vs Receivers")
    data = pd.DataFrame({
        "Category": ["Providers", "Receivers"],
        "Count": [total_providers, total_receivers]
    })
    st.bar_chart(data.set_index("Category"))

    conn.close()

# -------------------- SQL INSIGHTS --------------------
elif menu == "SQL Insights":
    st.header("📝 Automated SQL Insights")
    conn = get_connection()
    schema = build_schema(conn)
    queries = build_queries(schema)

    # If city filter is available, show it once for "Contact Info by City"
    if "3. Contact Info by City" in queries:
        # Pull unique cities from providers
        city_col = schema["p_city"]
        cities = pd.read_sql(f"SELECT DISTINCT {city_col} AS city FROM providers WHERE {city_col} IS NOT NULL;", conn)["city"].dropna().tolist()
        if cities:
            st.sidebar.markdown("**Filters**")
            selected_city = st.sidebar.selectbox("City (for contact info)", cities, index=0)
        else:
            selected_city = None
    else:
        selected_city = None

    # Render each query safely
    for title in [
        "1. Providers & Receivers per City",
        "2. Provider Type Contribution",
        "3. Contact Info by City",
        "4. Top Receivers by Claims",
        "5. Total Food Available",
        "6. City with Highest Listings",
        "7. Most Common Food Types",
        "8. Claims per Food Item",
        "9. Provider with Most Claims",
        "10. Claim Status Percentages",
        "11. Avg Quantity Claimed per Receiver",
        "12. Most Claimed Meal Type",
        "13. Total Food Donated per Provider",
        "14. City with Most Successful Claims",
        "15. Closest to Expiry but Available",
    ]:
        st.subheader(title)
        if title not in queries:
            st.info("Skipped: required columns not found in your database for this insight.")
            continue
        try:
            if title == "3. Contact Info by City" and selected_city is not None:
                df = pd.read_sql(queries[title], conn, params=[selected_city])
            else:
                df = pd.read_sql(queries[title], conn)

            if df.empty:
                st.warning("No data found for this query.")
            else:
                st.dataframe(df, use_container_width=True)
        except Exception as e:
            st.error(f"Error in query '{title}': {e}")
        time.sleep(0.3)

    conn.close()

# -------------------- CRUD OPERATIONS --------------------
elif menu == "CRUD":
    st.header("🛠️ CRUD Operations")
    conn = get_connection()
    cur = conn.cursor()

    crud_option = st.selectbox("Choose Operation", ["Create", "Read", "Update", "Delete"])

    # ---- CREATE ----
    if crud_option == "Create":
        st.subheader("➕ Add New Provider")
        with st.form("create_provider"):
            name = st.text_input("Name")
            city = st.text_input("City")
            provider_type = st.selectbox("Provider Type", ["Restaurant", "Household", "NGO"])
            submitted = st.form_submit_button("Add Provider")
            if submitted:
                cur.execute("INSERT INTO providers (name, city, provider_type) VALUES (?, ?, ?)", (name, city, provider_type))
                conn.commit()
                st.success("Provider added successfully!")

    # ---- READ ----
    elif crud_option == "Read":
        st.subheader("📖 View Data")
        table = st.selectbox("Select Table", ["providers", "receivers", "food_listings", "claims"])
        df = pd.read_sql(f"SELECT * FROM {table};", conn)
        st.dataframe(df)

    # ---- UPDATE ----
    elif crud_option == "Update":
        st.subheader("✏️ Update Provider Info")
        df = pd.read_sql("SELECT * FROM providers;", conn)
        provider_id = st.selectbox("Select Provider ID", df["provider_id"].tolist())
        new_name = st.text_input("New Name")
        new_city = st.text_input("New City")
        if st.button("Update"):
            cur.execute("UPDATE providers SET name=?, city=? WHERE provider_id=?", (new_name, new_city, provider_id))
            conn.commit()
            st.success("Provider updated successfully!")

    # ---- DELETE ----
    elif crud_option == "Delete":
        st.subheader("🗑️ Delete Provider")
        df = pd.read_sql("SELECT * FROM providers;", conn)
        provider_id = st.selectbox("Select Provider ID", df["provider_id"].tolist())
        if st.button("Delete"):
            cur.execute("DELETE FROM providers WHERE provider_id=?", (provider_id,))
            conn.commit()
            st.success("Provider deleted successfully!")

    conn.close()

# -------------------- EDA VISUALIZATIONS --------------------
elif menu == "EDA Visualizations":
    st.header("📈 Exploratory Data Analysis")
    conn = get_connection()
    schema = build_schema(conn)
    try:
        df = pd.read_sql("SELECT * FROM food_listings;", conn)
    except Exception:
        df = pd.DataFrame()
    conn.close()

    if not df.empty:
        # Map detected column names to EDA needs
        col_food_type = schema["f_food_type"]
        col_date = schema["f_date_posted"]
        col_qty = schema["f_quantity"]

        # Food type bar
        if col_food_type and col_food_type in df.columns:
            st.subheader("Bar Chart - Food Types")
            st.bar_chart(df[col_food_type].value_counts())
        else:
            st.warning("⚠️ Could not find a 'food type' column in food_listings.")

        # Quantity over time
        if col_date and col_qty and col_date in df.columns and col_qty in df.columns:
            st.subheader("Line Chart - Quantity Over Time")
            df[col_date] = pd.to_datetime(df[col_date], errors="coerce")
            ts = df.dropna(subset=[col_date]).groupby(col_date)[col_qty].sum().sort_index()
            st.line_chart(ts)

            st.subheader("Histogram - Quantity Distribution")
            fig, ax = plt.subplots()
            ax.hist(df[col_qty].dropna(), bins=20)
            st.pyplot(fig)
        else:
            st.info("⏩ Skipping time/quantity charts: required columns not found.")
    else:
        st.warning("No data found in food_listings table.")
