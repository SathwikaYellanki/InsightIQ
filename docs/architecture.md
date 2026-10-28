# InsightIQ — System Architecture

## 1. Project Overview

InsightIQ is an AI-assisted Business Intelligence application that allows users to ask questions about sales data in natural language. It uses Google Gemini to interpret questions and generate SQL queries, and displays the results through a Streamlit interface.

## 2. Technologies Used

- **Python:** Application logic.
- **Streamlit:** User interface.
- **Pandas:** CSV loading and data processing.
- **SQLite:** Storage and querying of sales transactions.
- **Google Gemini API:** Natural-language interpretation and SQL generation.
- **SQLGlot:** SQL parsing and validation.

## 3. Application Workflow

1. The user uploads a sales CSV file through the Streamlit interface.
2. The application reads the CSV using Pandas.
3. The application validates the data, handles invalid records, and prepares the sales data for querying.
4. The processed data is stored in or made available through an SQLite database table.
5. The user enters a natural-language question, such as "Which product generated the highest revenue?"
6. Gemini interprets the question and generates a proposed SQL query.
7. The application validates the generated SQL and restricts execution to permitted, read-only queries.
8. SQLite executes the validated query against the sales data.
9. The application displays the answer and supporting data in the Streamlit interface.

## 4. Architecture Diagram

```text
        User
         |
         v
  Streamlit Interface
         |
         v
  Upload Sales CSV
         |
         v
  Pandas Data Processing
         |
         v
    SQLite Database
         |
         v
  User's Natural-Language Question
         |
         v
     Google Gemini
         |
         v
   Generated SQL Query
         |
         v
  SQL Validation (SQLGlot)
         |
         v
  Execute Validated Query
         |
         v
    SQLite Results
         |
         v
  Answer and Supporting Data
         |
         v
   Streamlit Interface
```

## 5. Data Management

The application accepts sales transaction data in CSV format. Pandas is used to read and process the data. Invalid or incomplete records are handled according to the application's validation rules. SQLite provides a database for querying the processed sales data.

## 6. AI Integration and Security

Gemini is used to interpret natural-language questions and propose SQL queries. Generated SQL is validated before execution. The application is designed to allow only supported, read-only queries and to prevent destructive database operations.

## 7. Error Handling

The application should handle invalid questions, unsupported queries, malformed input data, SQL validation failures, and API errors without exposing sensitive information.

## 8. Limitations

- Results depend on the quality and completeness of the uploaded data.
- Ambiguous or unsupported questions may not produce a valid query.
- Gemini API availability and usage limits can affect responses.
- The application is a prototype and may require additional testing before production use.

## 9. Future Improvements

- Expand automated test coverage.
- Improve error handling and logging.
- Support more analytical question types.
- Improve query validation and handling of ambiguous questions.
- Add more detailed performance and reliability checks.