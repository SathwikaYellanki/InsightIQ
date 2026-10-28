# InsightIQ — AI Business Intelligence Assistant

## Overview

InsightIQ is a mini AI-powered Business Intelligence application that allows users to ask natural-language questions about sales transactions and receive answers supported by SQL queries and sales data.

## Features

- Upload sales data through a CSV file.
- Ask questions about revenue, products, regions, and monthly trends.
- Use Gemini to interpret questions and generate SQL queries.
- Validate generated SQL before execution.
- Display answers and supporting data.
- Handle invalid records and unsupported questions.
- Provide a simple Streamlit user interface.

## Technology Stack

- Python
- Streamlit
- Pandas
- SQLite
- Google Gemini API
- SQLGlot for SQL parsing and validation

## Setup and Execution

1. Install Python.
2. Clone this repository.
3. Open the project directory in a terminal.
4. Install dependencies:

   `pip install -r requirements.txt`

5. Configure your Gemini API key as an environment variable.

   PowerShell:

   `$env:GEMINI_API_KEY="YOUR_API_KEY"`

6. Start the application:

   `python -m streamlit run app.py`

7. Open the local URL displayed in the terminal.

## Data Handling

The application accepts a sales CSV, validates the required fields, handles invalid records, and prepares the data for querying. Refer to the source code for the exact validation and revenue calculation rules.

## AI and SQL Safety

Gemini interprets natural-language questions and proposes SQL. The application validates the query and restricts execution to supported, read-only queries against the sales data.

## Testing

Automated tests are maintained in the `tests/` directory. Run them using:

`python -m pytest -v`

## Limitations

- Results depend on the quality and completeness of the uploaded data.
- AI-generated SQL may fail for unsupported or ambiguous questions.
- Gemini API availability and usage limits can affect responses.
- The application is a prototype, not a production-ready BI platform.

## Future Improvements

- Add more automated tests and query examples.
- Improve handling of ambiguous questions.
- Add API endpoints if required.
- Improve query performance, logging, and error reporting.