import pandas as pd
import json
import boto3

# Load the CSV file
df = pd.read_csv('Member Info - Fall 2024.csv', skiprows=1)

# Specify the columns you want to keep
columns_to_keep = ['Name', 'Slack ID', 'Subteam']  # Replace with your desired column names

# Create a new DataFrame with only the desired columns
df_filtered = df[columns_to_keep]

# Specify the column you want to sort by (assuming "subteam" is one of the columns in the second row)
sort_column = 'Subteam'  # Replace with the actual name of the column you want to sort by

# Define the allowed values for the "subteam" column
subteam_order = ["TL", "Marketing", "Autonomous", "Kinetic", "Sportsman"]

# Convert the column to a categorical type with the specific order
df_filtered[sort_column] = pd.Categorical(df_filtered[sort_column], categories=subteam_order, ordered=True)

# Sort the DataFrame by the specified column
df_sorted = df_filtered.sort_values(by=sort_column)

# Filter the DataFrame to keep only rows where "subteam" column has values in subteam_order
df_filtered = df_sorted[df_sorted[sort_column].isin(subteam_order)]

# Create a string of all Slack IDs, separated by a comma and a space
slack_ids = ', '.join(f'"{slack_id}"' for slack_id in df_filtered['Slack ID'])

print(slack_ids)

# Save the filtered DataFrame to a new CSV file
# df_filtered.to_csv('filtered_sorted_output.csv', index=False)

# # Convert the DataFrame to a list of dictionaries
# records = df_filtered.to_dict(orient='records')

# # Prepare JSON format for DynamoDB
# dynamodb_items = []
# for record in records:
#     item = {k: v for k, v in record.items()}  # Assume all data is string type
#     dynamodb_items.append(item)

# # Save to a JSON file
# with open('dynamodb_data.json', 'w') as f:
#     import json
#     json.dump(dynamodb_items, f, indent=4)

# # Initialize a session using Amazon DynamoDB
# session = boto3.Session(
#     aws_access_key_id='AKIA47CRW7XYNFJEQNOD',
#     aws_secret_access_key='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',
#     region_name='us-east-1'
# )

# # Initialize DynamoDB resource
# dynamodb = session.resource('dynamodb')
# table = dynamodb.Table('CRC_MEMBER_INFO')  

# # Load the JSON data
# with open('dynamodb_data.json') as json_file:
#     items = json.load(json_file)
#     for item in items:
#         table.put_item(Item=item)