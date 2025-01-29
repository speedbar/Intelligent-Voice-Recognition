import pandas as pd
import json
import os

def convert_all_excel_to_json(directory_path):
    """
    Convert all Excel files in a specified directory to JSON files.
    
    Parameters:
        directory_path (str): Path to the directory containing Excel files.
    """
    try:
        # List all files in the directory
        files = os.listdir(directory_path)
        
        # Filter Excel files
        excel_files = [file for file in files if file.endswith(('.xls', '.xlsx'))]
        
        if not excel_files:
            print(f"No Excel files found in directory: {directory_path}")
            return
        
        # Process each Excel file
        for excel_file in excel_files:
            input_excel_file = os.path.join(directory_path, excel_file)
            
            # Generate JSON file name
            output_json_file = os.path.join(directory_path, os.path.splitext(excel_file)[0] + '.json')
            
            try:
                # Read the Excel file
                df = pd.read_excel(input_excel_file)
                
                # Convert Timestamp objects to strings
                for col in df.select_dtypes(include=['datetime64[ns]', 'datetime64[ns, UTC]']):
                    df[col] = df[col].dt.strftime('%Y-%m-%dT%H:%M:%S')  # ISO 8601 format
                
                # Convert the DataFrame to a dictionary
                data = df.to_dict(orient='records')
                
                # Write the dictionary to a JSON file
                with open(output_json_file, 'w', encoding='utf-8') as json_file:
                    json.dump(data, json_file, indent=4, ensure_ascii=False)
                
                print(f"Successfully converted '{input_excel_file}' to '{output_json_file}'")
            except Exception as file_error:
                print(f"Error processing file '{excel_file}': {file_error}")
    except Exception as e:
        print(f"An error occurred: {e}")

# Example usage
directory_path = r'C:\Users\nthenutt\OneDrive - Capgemini\Documents\JLP-IVR'
convert_all_excel_to_json(directory_path)
