# from selenium import webdriver
# from selenium.webdriver.common.by import By
# from selenium.webdriver.support.ui import WebDriverWait, Select
# from selenium.webdriver.support import expected_conditions as EC
# from datetime import datetime
# import pandas as pd
# import time
# import requests
import csv
import psycopg2
from psycopg2 import sql
import chardet

## SOLUTION #1: BEGIN THE CAPTCHA SOLVED USING 2CAPTCHA (We need to pay for it: https://2captcha.com/enterpage)

# # Set up the WebDriver (make sure the driver is in your PATH)
# driver = webdriver.Chrome()

# # Open the webpage
# driver.get('https://service.yukon.ca/apps/contract-registry')

# driver.save_screenshot("captcha.png")

# # Upload the CAPTCHA image to 2Captcha
# api_key = '2c33ca4e0cc4ad9ec06f50e8c4a3eea9'
# captcha_file = {'file': open('captcha.png', 'rb')}
# response = requests.post(f"http://2captcha.com/in.php?key={api_key}&method=post", files=captcha_file)

# print(response.text)
# captcha_id = response.text.split('|')[1]

# # Wait for the CAPTCHA to be solved
# result = requests.get(f"http://2captcha.com/res.php?key={api_key}&action=get&id={captcha_id}")
# while 'CAPCHA_NOT_READY' in result.text:
#     time.sleep(5)
#     result = requests.get(f"http://2captcha.com/res.php?key={api_key}&action=get&id={captcha_id}")

# # Get the CAPTCHA solution
# captcha_solution = result.text.split('|')[1]

# # Enter the solution on the page
# captcha_input = driver.find_element(By.ID, 'cf-chl-widget-s1xxq_response')
# captcha_input.send_keys(captcha_solution)
# time.sleep(5)

## END THE CAPTCHA SOLVED USING 2CAPTCHA (We need to pay for it: https://2captcha.com/enterpage)


## SOLUTION #2:  SOLVED CAPTCHA USING COOKIES
# # Add cookies manually here
# cookies = [
#     {'name': '__insp_slim', 'value': '1712354417795', 'domain': 'service.yukon.ca'},
#     {'name': '__insp_norec_sess', 'value': 'true', 'domain': 'service.yukon.ca'},
#     {'name': '__insp_targlpu', 'value': 'aHR0cHM6Ly93d3cuc2VydmljZS55dWtvbi5jYS9hcHBzL2NvbnRyYWN0LXJlZ2lzdHJ5', 'domain': 'service.yukon.ca'},
#     {'name': '__insp_nv', 'value': 'true', 'domain': 'service.yukon.ca'},
#     {'name': '__insp_wid', 'value': '963829658', 'domain': 'service.yukon.ca'},
#     {'name': 'cf_clearance', 'value': 'k8NSmc9fVaUsMKJusSh01DD9p6_7w1mOYIB3ccVfxrY-1725393548-1.2.1.1-wSFpewMdApLeTYWvLSJYmGwTpza1aRxlKnMyf28_eOPtWsRAee_mXEGUSWntivS0N6pFyrycZBJofqAXfl6s.ZqIR.Yxz3qmxU2ces7nWR0Jbr9ig2rAfePXvIeUZjJ._ALjFxCenTTz9qS4pAWoDMi9L6SIGzFFBOF527QtuAc.KmiV6YACZn9oqCPOgefx.wHibJmzPOqB_2P2GRBUVal_na0GRBJ6sTiAtz_6tPOKFnwTbCSAbx2CAXjRVC5QtsLms18qDbF8qS6mtjKVOC4JOjFeJq_naEREYXdNN2qbC_cAIVFdtYwLpJ1Klo5oUTd1QOvbnAS.8uQ4tYk3dfWAu1eCaFnsqRQjZsKgZu8L.E.ehhjMRApl.mbdKiczYkZzDXeiILLMQ38f3EwiXRHgzv7MQVeDR9xvxDwPMWe6wJ8FHcTQ0eEQ9keku4KePGcjXM4TwiQWR_.3_o2i_w', 'domain': 'service.yukon.ca'},
#     {'name': '_cf_bm', 'value': 'JJPGjilLAxqEXlVAyOf0fPEveCZZ7BT_3j9kXk9qYHaM-1725393548-1.0.1.1-r1kKt2rBYvHnmtqrHb_TpncZQd9Bwcvajelg', 'domain': 'service.yukon.ca'},
#     {'name': '__insp_targlpt', 'value': '', 'domain': 'service.yukon.ca'},  # Este valor está vacío
#     {'name': '_ga_PSQGYLC55F', 'value': 'GS1.3.1712354046.1.1.1712354417.60.0.0', 'domain': 'service.yukon.ca'},
#     {'name': '_pk_ses.65.9010', 'value': '1', 'domain': 'service.yukon.ca'},
#     {'name': 'SESS4be9cb88ddc66040fdff761e15eef699', 'value': 'rSaFhEusb_l6vODTfdnyLUXk4soBydxov5fPkJLQTPg', 'domain': 'service.yukon.ca'},
#     {'name': '_ga_W67GNQ4E0R', 'value': 'GS1.3.1720049478.1.0.1720049478.0.0.0', 'domain': 'service.yukon.ca'},
#     {'name': '_ga', 'value': 'GA1.3.182981680.1712354046', 'domain': 'service.yukon.ca'},
#     {'name': '_pk_id.65.9010', 'value': '96e6d6de81a08e829e.1724953844.', 'domain': 'service.yukon.ca'},
#     {'name': '_pk_id.49.9010', 'value': '159d94e082524c04.1724716252.', 'domain': 'service.yukon.ca'},
#     {'name': 'ORA_WWV_APP_108', 'value': 'ORA_WWV-E3ZSdg1YzawTb_j-CvhKVVIG', 'domain': 'service.yukon.ca'}
# ]

# for cookie in cookies:
#     driver.add_cookie(cookie)
# time.sleep(50)  # Adjust based on file size

# # Reload the page to apply the cookies
# driver.refresh()





## BEGIN: FILLED THE SEARCH PAGE
# Wait until the element is present in the DOM and visible
# select_element_start = WebDriverWait(driver, 20).until(
#     EC.presence_of_element_located((By.ID, 'P500_FISCAL_YEAR_FROM'))
# )

# select_element_end = WebDriverWait(driver, 20).until(
#     EC.presence_of_element_located((By.ID, 'P500_FISCAL_YEAR_TO'))
# )

# select_start = Select(select_element_start)
# select_end = Select(select_element_end)

# # Check if the select has available options
# end_options = select_end.options

# if len(end_options) > 0:
#     print(f"Options found: {len(end_options)}")
#     # Select the last option in the start
#     select_start.select_by_index(len(select_start.options) - 1)  # Last option

#     # Select the first option in the end
#     #select_end.select_by_index(0)  # First option
# else:
#     print("No options found in select_end.")

#     # Print the element's HTML for manual inspection
#     print(driver.execute_script("return arguments[0].outerHTML;", select_element_end))


# time.sleep(2)  # Adjust the time as necessary

# # Wait until the button is available and select the button based on the class and text
# search_button = WebDriverWait(driver, 20).until(
#     EC.element_to_be_clickable((By.XPATH, "//button[contains(@class, 't-Button--hot') and .//span[text()='Search']]"))
# )

# # Click the button
# search_button.click()


# Wait for the new page to load
# time.sleep(5)  # Adjust the time as necessary

# END: FILLED THE SEARCH PAGE

# BEGIN: DOWNLOAD

# Download the Excel file

# download_button = WebDriverWait(driver, 20).until(
#     EC.element_to_be_clickable((By.XPATH, "//button[contains(@class, 't-Button--hot') and .//span[text()='Download CSV']]"))
# )
# # Click the button
# download_button.click()


# # Wait for the download to complete
# time.sleep(50)  # Adjust based on file size
# END: DOWNLOAD


# Close the browser
# driver.quit()

# Connect to the PostgreSQL database
conn = psycopg2.connect(
    host="localhost",  # Change to your host if necessary
    database="bizont_contract_registry",  # Change to your database name
    user="bizont",  # Change to your PostgreSQL user
    password="Gedani15"  # Change to your PostgreSQL password
)

# Create a cursor to execute SQL queries
cur = conn.cursor()

cur.execute("TRUNCATE TABLE temp_contracts;")

# Read the CSV file
with open('downloads/contract_list_04_09_2024.csv', mode='r', encoding='ISO-8859-1') as file:
    # Use DictReader so the first row is treated as a header
    reader = csv.DictReader(file)
    
    inserted_count = 0  # Counter for rows inserted successfully
    failed_rows = []  # List to store rows that failed to insert

    for row in reader:
        try:
            # Clean and process the values before insertion
            row_data = (
                row['Contract Description'],
                row['Vendor Name'],
                row['Amount'].replace('$', '').replace(',', '').replace('-', '0'),  # Remove dollar signs, commas, and handle negative values
                row['Start Date'],
                row['Contract No.'],
                row['C.O. No.'],
                row['Line No'],
                row['SOA No.'],
                row['Department'],
                row['Contract Type'],
                row['Tender Class'],
                row['Community'],
                row['Type'],
                row['Fiscal Year']
            )

            cur.execute("""
                INSERT INTO temp_contracts (
                    contract_description, vendor_name, amount, start_date, contract_no,
                    co_no, line_no, soa_no, department, contract_type, tender_class, community, type, fiscal_year
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, row_data)
            inserted_count += 1

        except Exception as e:
            failed_rows.append(row)  # Add the row to the list of failures
            print(f"Error in the row: {row}")
            print(f"Error: {e}")
            conn.rollback()  # Rollback the current transaction for this row
    
    try:
        conn.commit()  # Confirm all correct insertions
         print(f"Data confirmed. {inserted_count} rows inserted successfully in temp_contracts.")
    except Exception as e:
        print(f"Error committing to the database: {e}")

    # Show failed rows
    if failed_rows:
        print(f"We found {len(failed_rows)} rows that could not be inserted:")
        for failed_row in failed_rows:
            print(failed_row)
try:
    # Execute the procedure for processing the data
    cur.execute("SELECT process_contracts();")
except Exception as e:
        print(f"Error to execute process: {e}")

conn.commit()

# Close cursor and connection
cur.close()
conn.close()

print("Process completed.")