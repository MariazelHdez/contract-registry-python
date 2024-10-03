import csv
import psycopg2
import os
import glob

# Access environment variables
db_user = os.getenv('DB_USER')
db_password = os.getenv('DB_PASSWORD')
db_host = os.getenv('DB_HOST')
db_name = os.getenv('DB_NAME')
db_port = os.getenv('DB_PORT')

# Connect to the PostgreSQL database
conn = psycopg2.connect(
    host=db_host,  
    database=db_name,
    user=db_user,
    password=db_password, 
    port=db_port
)

# Create a cursor to execute SQL queries
cur = conn.cursor()

# Directory where the CSV files are located
directory = '/Users/mariazelhernandez/Bizont-Code/contract-registry-python/downloads'

# Find all CSV files in the directory
csv_files = glob.glob(os.path.join(directory, '*.csv'))
if not csv_files:
    print("No CSV files found in the 'downloads' folder.")
else:
    inserted_count = 0  # Counter for successfully inserted rows
    failed_rows = []  # List to store rows that failed

    # Process each CSV file in the folder
    for csv_file in csv_files:
        cur.execute("TRUNCATE TABLE temp_contracts;")
        print(f"Processing file: {csv_file}")
        # Read the CSV file
        with open(csv_file, mode='r', encoding='ISO-8859-1') as file:
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
                print(f"Error executing process: {e}")

conn.commit()

# Close cursor and connection
cur.close()
conn.close()

print("Process completed.")