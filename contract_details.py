import os
import json
import time
from typing import List, Tuple

import psycopg2
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC

URL = "https://service.yukon.ca/apps/contract-registry"
JSON_FILE = "contract_details_progress.json"


def get_db_connection():
    """Create a database connection using environment variables."""
    return psycopg2.connect(
        host=os.getenv("DB_HOST"),
        database=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        port=os.getenv("DB_PORT"),
    )


def fetch_contract_numbers(conn) -> List[str]:
    """Fetch all contract numbers from the database."""
    with conn.cursor() as cur:
        cur.execute("SELECT contract_no FROM contracts ORDER BY contract_no")
        rows = cur.fetchall()
    return [row[0] for row in rows]


def insert_details(conn, details: List[dict]):
    """Insert the collected contract details into the database."""
    with conn.cursor() as cur:
        for item in details:
            cur.execute(
                """
                INSERT INTO contract_details
                    (contract_no, description, vendor, amount, fiscal_year)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (contract_no) DO NOTHING
                """,
                (
                    item.get("contract_no"),
                    item.get("description"),
                    item.get("vendor"),
                    item.get("amount"),
                    item.get("fiscal_year"),
                ),
            )
    conn.commit()


def load_progress() -> Tuple[List[str], List[dict]]:
    """Load processed contract numbers and data from JSON_FILE."""
    if not os.path.exists(JSON_FILE):
        return [], []
    with open(JSON_FILE, "r") as f:
        data = json.load(f)
    return data.get("processed", []), data.get("details", [])


def save_progress(processed: List[str], details: List[dict]):
    """Save processed contract numbers and scraped details to JSON_FILE."""
    with open(JSON_FILE, "w") as f:
        json.dump({"processed": processed, "details": details}, f, indent=2)


def setup_driver() -> webdriver.Chrome:
    """Initialize a Selenium Chrome driver."""
    options = webdriver.ChromeOptions()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    driver = webdriver.Chrome(options=options)
    driver.set_page_load_timeout(60)
    return driver


def search_contract(driver: webdriver.Chrome, contract_no: str) -> dict:
    """Search for a contract and return scraped details."""
    driver.get(URL)

    WebDriverWait(driver, 20).until(
        EC.presence_of_element_located((By.ID, "P500_FISCAL_YEAR_FROM"))
    )

    select_start = Select(driver.find_element(By.ID, "P500_FISCAL_YEAR_FROM"))
    select_end = Select(driver.find_element(By.ID, "P500_FISCAL_YEAR_TO"))

    select_start.select_by_visible_text("2007-08")
    WebDriverWait(driver, 20).until(
        lambda d: len(select_end.options) > 1
    )
    select_end.select_by_visible_text("2025-26")

    search_box = driver.find_element(By.ID, "P500_CONTRACT_NO")
    search_box.clear()
    search_box.send_keys(contract_no)

    search_button = driver.find_element(
        By.XPATH, "//button[contains(@class,'t-Button') and .//span[text()='Search']]"
    )
    search_button.click()

    WebDriverWait(driver, 20).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, "#report_table_P500_RESULTS"))
    )

    row = driver.find_element(
        By.XPATH,
        f"//table[@id='report_table_P500_RESULTS']//td[normalize-space()='{contract_no}']/..",
    )
    row.click()

    WebDriverWait(driver, 20).until(
        EC.presence_of_element_located((By.ID, "P520_DESCRIPTION_CONTAINER"))
    )

    description = driver.find_element(By.ID, "P520_DESCRIPTION").text
    vendor = driver.find_element(By.ID, "P520_VENDOR_NAME").text
    amount = driver.find_element(By.ID, "P520_AMOUNT").text
    fiscal_year = driver.find_element(By.ID, "P520_FISCAL_YEAR").text

    return {
        "contract_no": contract_no,
        "description": description,
        "vendor": vendor,
        "amount": amount,
        "fiscal_year": fiscal_year,
    }


def main():
    conn = get_db_connection()
    numbers = fetch_contract_numbers(conn)
    processed, details = load_progress()
    driver = setup_driver()

    try:
        for num in numbers:
            if num in processed:
                continue
            try:
                info = search_contract(driver, num)
                details.append(info)
                processed.append(num)
                save_progress(processed, details)
            except Exception:
                driver.quit()
                time.sleep(2)
                driver = setup_driver()
                continue
    finally:
        driver.quit()

    insert_details(conn, details)
    conn.close()


if __name__ == "__main__":
    main()
