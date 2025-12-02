"""
Regression tests for date formatting bug in _build_search_criteria.

This test file specifically documents and tests the fix for the bug where
the date formatting used .upper() which converted date strings like
'24-Nov-2025' to '24-NOV-2025', causing Yandex IMAP to reject the format.

Bug Details:
- Location: mcp_email_server/emails/classic.py, EmailClient._build_search_criteria()
- Lines affected: 191 and 193
- Issue: Used .strftime("%d-%b-%Y").upper() instead of .strftime("%d-%b-%Y")
- Impact: Yandex IMAP servers reject uppercase month abbreviations
- Fix: Removed .upper() calls

These tests ensure that if someone accidentally adds .upper() back,
the tests will immediately fail, preventing the bug from reoccurring.
"""

from datetime import datetime, timezone

from mcp_email_server.emails.classic import EmailClient


class TestDateFormatRegression:
    """
    Test suite specifically for the date formatting regression.

    IMAP RFC 3501 specifies date format as: date-day "-" date-month "-" date-year
    where date-month is the three-letter abbreviation with proper case (not uppercase).
    """

    def test_november_2025_exact_bug_case(self):
        """
        Test the exact date from the bug report: November 24, 2025.

        This was the specific example that failed with Yandex IMAP when
        formatted as '24-NOV-2025' instead of '24-Nov-2025'.
        """
        nov_24_2025 = datetime(2025, 11, 24, tzinfo=timezone.utc)

        # Test BEFORE
        criteria = EmailClient._build_search_criteria(before=nov_24_2025)
        assert criteria == ["BEFORE", "24-Nov-2025"], "Date should use 'Nov' not 'NOV'"

        # Test SINCE
        criteria = EmailClient._build_search_criteria(since=nov_24_2025)
        assert criteria == ["SINCE", "24-Nov-2025"], "Date should use 'Nov' not 'NOV'"

    def test_date_not_uppercase(self):
        """
        Verify that date strings are NOT in uppercase.

        This test will fail if someone adds .upper() back to the code.
        """
        test_date = datetime(2025, 11, 24, tzinfo=timezone.utc)
        criteria = EmailClient._build_search_criteria(before=test_date)

        date_string = criteria[1]

        # The date string should NOT be all uppercase
        assert date_string != date_string.upper(), "Date string must not be uppercase"

        # Specifically check that 'Nov' is not 'NOV'
        assert "NOV" not in date_string, "Month abbreviation must not be uppercase"
        assert "Nov" in date_string, "Month abbreviation must be properly capitalized"

    def test_month_abbreviation_case_sensitive(self):
        """
        Test that month abbreviations have proper capitalization (Title case).

        IMAP RFC expects: Jan, Feb, Mar, Apr, May, Jun, Jul, Aug, Sep, Oct, Nov, Dec
        NOT: JAN, FEB, MAR, APR, MAY, JUN, JUL, AUG, SEP, OCT, NOV, DEC
        """
        expected_months = {
            1: "Jan",
            2: "Feb",
            3: "Mar",
            4: "Apr",
            5: "May",
            6: "Jun",
            7: "Jul",
            8: "Aug",
            9: "Sep",
            10: "Oct",
            11: "Nov",
            12: "Dec",
        }

        for month_num, expected_abbr in expected_months.items():
            test_date = datetime(2025, month_num, 15, tzinfo=timezone.utc)
            criteria = EmailClient._build_search_criteria(before=test_date)
            date_string = criteria[1]

            # Check that the expected month abbreviation is present
            assert (
                expected_abbr in date_string
            ), f"Expected '{expected_abbr}' in date string, got '{date_string}'"

            # Ensure the uppercase version is NOT present
            uppercase_abbr = expected_abbr.upper()
            if uppercase_abbr != expected_abbr:  # Skip "May" which is same in both cases
                assert (
                    uppercase_abbr not in date_string
                ), f"Uppercase '{uppercase_abbr}' should not be in date string '{date_string}'"

    def test_date_format_matches_rfc3501(self):
        """
        Test that date format matches IMAP RFC 3501 specification.

        Format should be: DD-Mmm-YYYY
        - DD: 2-digit day (01-31)
        - Mmm: 3-letter month abbreviation, capitalized (Jan, Feb, etc.)
        - YYYY: 4-digit year

        Example: "24-Nov-2025"
        """
        test_cases = [
            (datetime(2025, 11, 24, tzinfo=timezone.utc), "24-Nov-2025"),
            (datetime(2023, 1, 1, tzinfo=timezone.utc), "01-Jan-2023"),
            (datetime(2023, 12, 31, tzinfo=timezone.utc), "31-Dec-2023"),
            (datetime(2024, 2, 29, tzinfo=timezone.utc), "29-Feb-2024"),  # Leap year
        ]

        for test_date, expected_format in test_cases:
            criteria = EmailClient._build_search_criteria(before=test_date)
            actual_format = criteria[1]
            assert (
                actual_format == expected_format
            ), f"Expected '{expected_format}', got '{actual_format}'"

    def test_both_before_and_since_use_proper_case(self):
        """
        Test that both BEFORE and SINCE parameters use proper case for dates.

        The bug affected both lines 191 (BEFORE) and 193 (SINCE).
        """
        before_date = datetime(2025, 11, 24, tzinfo=timezone.utc)
        since_date = datetime(2025, 1, 15, tzinfo=timezone.utc)

        criteria = EmailClient._build_search_criteria(before=before_date, since=since_date)

        # Should be: ["BEFORE", "24-Nov-2025", "SINCE", "15-Jan-2025"]
        assert len(criteria) == 4
        assert criteria[0] == "BEFORE"
        assert criteria[1] == "24-Nov-2025", "BEFORE date should use proper case"
        assert criteria[2] == "SINCE"
        assert criteria[3] == "15-Jan-2025", "SINCE date should use proper case"

        # Verify neither date is uppercase
        assert "NOV" not in criteria[1], "BEFORE date must not use uppercase month"
        assert "JAN" not in criteria[3], "SINCE date must not use uppercase month"

    def test_yandex_imap_compatibility(self):
        """
        Test that the format is compatible with Yandex IMAP servers.

        Yandex IMAP servers reject uppercase month abbreviations.
        This test documents the specific requirement.
        """
        # Use a variety of dates to ensure compatibility
        test_dates = [
            datetime(2025, 1, 15, tzinfo=timezone.utc),
            datetime(2025, 6, 30, tzinfo=timezone.utc),
            datetime(2025, 11, 24, tzinfo=timezone.utc),
            datetime(2025, 12, 25, tzinfo=timezone.utc),
        ]

        for test_date in test_dates:
            criteria = EmailClient._build_search_criteria(before=test_date)
            date_string = criteria[1]

            # Verify the format matches: DD-Mmm-YYYY (not DD-MMM-YYYY)
            parts = date_string.split("-")
            assert len(parts) == 3, f"Date should have 3 parts separated by '-', got: {date_string}"

            day, month, year = parts

            # Day should be 2 digits
            assert len(day) == 2, f"Day should be 2 digits, got: {day}"
            assert day.isdigit(), f"Day should be numeric, got: {day}"

            # Month should be 3 characters, title case (first letter upper, rest lower)
            assert len(month) == 3, f"Month should be 3 characters, got: {month}"
            assert month[0].isupper(), f"Month first letter should be uppercase, got: {month}"
            assert month[1:].islower(), f"Month rest should be lowercase, got: {month}"

            # Year should be 4 digits
            assert len(year) == 4, f"Year should be 4 digits, got: {year}"
            assert year.isdigit(), f"Year should be numeric, got: {year}"
