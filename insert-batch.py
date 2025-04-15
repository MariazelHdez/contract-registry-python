import csv
import psycopg2
import os
import glob
from dotenv import load_dotenv
from psycopg2.extras import execute_values

# Cargar variables de entorno
load_dotenv()

# Acceder a las credenciales de la base de datos
db_user = os.getenv('DB_USER')
db_password = os.getenv('DB_PASSWORD')
db_host = os.getenv('DB_HOST')
db_name = os.getenv('DB_NAME')
db_port = os.getenv('DB_PORT')

# Conectar a la base de datos PostgreSQL
conn = psycopg2.connect(
    host=db_host,  
    database=db_name,
    user=db_user,
    password=db_password, 
    port=db_port
)

# Crear cursor para ejecutar consultas SQL
cur = conn.cursor()

# Directorio donde están los archivos CSV
directory = './downloads'

# Buscar todos los archivos CSV en el directorio
csv_files = glob.glob(os.path.join(directory, '*.csv'))
if not csv_files:
    print("No CSV files found in the 'downloads' folder.")
else:
    for csv_file in csv_files:
        cur.execute("TRUNCATE TABLE temp_contracts;")  # Limpiar la tabla antes de insertar datos
        print(f"Processing file: {csv_file}")

        # Leer el archivo CSV
        with open(csv_file, mode='r', encoding='ISO-8859-1') as file:
            reader = csv.DictReader(file)
            
            batch_size = 1000  # Cantidad de registros por lote
            batch = []  # Lista temporal para los lotes

            inserted_count = 0
            failed_rows = []

            for row in reader:
                try:
                    # Procesar y limpiar datos
                    row_data = (
                        row['Contract Description'],
                        row['Vendor Name'],
                        row['Amount'].replace('$', '').replace(',', '').replace('-', '0'),  # Limpiar montos
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

                    batch.append(row_data)

                    # Si el batch alcanza el límite, inserta en la BD
                    if len(batch) >= batch_size:
                        execute_values(cur, """
                            INSERT INTO temp_contracts (
                                contract_description, vendor_name, amount, start_date, contract_no,
                                co_no, line_no, soa_no, department, contract_type, tender_class, community, type, fiscal_year
                            ) VALUES %s
                        """, batch)
                        
                        conn.commit()  # Confirmar inserción
                        inserted_count += len(batch)
                        batch = []  # Vaciar batch

                except Exception as e:
                    failed_rows.append(row)
                    print(f"Error in the row: {row}")
                    print(f"Error: {e}")

            # Insertar los datos restantes si hay menos de 1000
            if batch:
                execute_values(cur, """
                    INSERT INTO temp_contracts (
                        contract_description, vendor_name, amount, start_date, contract_no,
                        co_no, line_no, soa_no, department, contract_type, tender_class, community, type, fiscal_year
                    ) VALUES %s
                """, batch)
                
                conn.commit()
                inserted_count += len(batch)

        print(f"Total rows inserted in temp_contracts: {inserted_count}")

        # Ejecutar el procedimiento para procesar datos
        try:
            cur.execute("SELECT process_contracts();")
            conn.commit()
            print("Procedure process_contracts() executed successfully.")
        except Exception as e:
            print(f"Error executing process_contracts(): {e}")

        # Mostrar filas fallidas
        if failed_rows:
            print(f"{len(failed_rows)} rows could not be inserted:")
            for failed_row in failed_rows:
                print(failed_row)

# Cerrar conexión
cur.close()
conn.close()

print("Process completed.")