import io

import pandas as pd
import pytest

import app


@pytest.fixture
def sample_csv():
    """Create a small sales CSV for testing."""
    return io.StringIO(
        "Transaction_Id,Order_date,Product,Category,Region,Sales_channel,"
        "Quantity,Unit_price,Discount_amount,Status\n"
        "TXN001,03-01-2026,Laptop Stand,Accessories,North,Online,2,1200,100,Completed\n"
        "TXN002,05-01-2026,Wireless Mouse,Accessories,South,Retail,3,650,0,Completed\n"
        "TXN003,08-01-2026,Monitor,Electronics,East,Online,2,9500,500,Returned\n"
    )


@pytest.fixture
def test_db(tmp_path):
    """Create an isolated temporary SQLite database."""
    return str(tmp_path / "test_sales.db")


def test_valid_csv_is_loaded(sample_csv):
    """Test that valid completed transactions are loaded."""
    df, raw_count, excluded_count = app.read_sales_csv(sample_csv)

    assert raw_count == 3
    assert len(df) == 2
    assert excluded_count == 1


def test_revenue_is_calculated_correctly():
    """Test revenue calculation after discount."""
    csv_data = io.StringIO(
        "Transaction_Id,Order_date,Product,Category,Region,Sales_channel,"
        "Quantity,Unit_price,Discount_amount,Status\n"
        "TXN001,03-01-2026,Laptop Stand,Accessories,North,Online,2,1200,100,Completed\n"
    )

    df, _, _ = app.read_sales_csv(csv_data)

    assert df.iloc[0]["Revenue"] == 2300


def test_missing_required_column_is_rejected():
    """Test that a CSV without required columns raises an error."""
    invalid_csv = io.StringIO(
        "Transaction_Id,Product,Quantity\n"
        "TXN001,Laptop Stand,2\n"
    )

    with pytest.raises(ValueError, match="Missing required columns"):
        app.read_sales_csv(invalid_csv)


def test_data_is_stored_and_queried(test_db):
    """Test that sales data can be stored and queried in SQLite."""
    df = pd.DataFrame(
        {
            "Transaction_Id": ["TXN001", "TXN002"],
            "Product": ["Laptop Stand", "Wireless Mouse"],
            "Revenue": [2300, 1950],
        }
    )

    app.store_data(df, test_db)
    result = app.query_db(
        "SELECT SUM(Revenue) AS total_revenue FROM sales",
        db_path=test_db,
    )

    assert result.iloc[0]["total_revenue"] == 4250


def test_destructive_sql_is_rejected(test_db):
    """Test that DELETE statements cannot pass SQL validation."""
    df = pd.DataFrame(
        {
            "Transaction_Id": ["TXN001"],
            "Product": ["Laptop Stand"],
            "Revenue": [2300],
        }
    )
    app.store_data(df, test_db)

    with pytest.raises(ValueError):
        app.validate_sales_sql("DELETE FROM sales", db_path=test_db)


def test_query_db_rejects_non_select_statements(test_db):
    """Test that the database helper rejects UPDATE statements."""
    with pytest.raises(ValueError, match="Only read-only SELECT"):
        app.query_db(
            "UPDATE sales SET Revenue = 0",
            db_path=test_db,
        )
