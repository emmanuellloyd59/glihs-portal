"""Regression tests for SQL placeholder adaptation."""
import unittest

from db.sql import SQLPlaceholderError, qmark_to_format


class QmarkToFormatTests(unittest.TestCase):
    def test_converts_only_requested_placeholders(self):
        self.assertEqual(
            qmark_to_format("SELECT * FROM users WHERE id = ? AND role = ?", 2),
            "SELECT * FROM users WHERE id = %s AND role = %s",
        )

    def test_preserves_question_marks_in_strings_and_identifiers(self):
        statement = """SELECT '?' AS literal, "odd?name" FROM records WHERE id = ?"""
        self.assertEqual(
            qmark_to_format(statement, 1),
            """SELECT '?' AS literal, "odd?name" FROM records WHERE id = %s""",
        )

    def test_preserves_line_and_nested_block_comments(self):
        statement = "SELECT ? -- ignore ?\n/* outer ? /* nested ? */ still ? */ WHERE id = ?"
        self.assertEqual(
            qmark_to_format(statement, 2),
            "SELECT %s -- ignore ?\n/* outer ? /* nested ? */ still ? */ WHERE id = %s",
        )

    def test_preserves_dollar_quoted_function_body(self):
        statement = "CREATE FUNCTION f() RETURNS text AS $body$ SELECT '?'; $body$ LANGUAGE SQL; SELECT ?"
        self.assertEqual(
            qmark_to_format(statement, 1),
            "CREATE FUNCTION f() RETURNS text AS $body$ SELECT '?'; $body$ LANGUAGE SQL; SELECT %s",
        )

    def test_preserves_escape_string_backslash_quotes(self):
        statement = r"SELECT E'escaped \\' ? text' AS value WHERE id = ?"
        self.assertEqual(
            qmark_to_format(statement, 1),
            r"SELECT E'escaped \\' ? text' AS value WHERE id = %s",
        )

    def test_unterminated_lexical_constructs_fail_closed(self):
        for statement in ("SELECT 'unfinished ?", 'SELECT "unfinished ?', "SELECT /* unfinished ?", "SELECT $ unfinished ?"):
            with self.subTest(statement=statement):
                with self.assertRaises(SQLPlaceholderError):
                    qmark_to_format(statement, 0)

    def test_preserves_psycopg_escaped_question_operator(self):
        self.assertEqual(qmark_to_format("payload ?? 'key' WHERE id = ?", 1),
                         "payload ?? 'key' WHERE id = %s")

    def test_parameter_count_mismatch_fails_closed(self):
        with self.assertRaises(SQLPlaceholderError):
            qmark_to_format("SELECT ?", 0)
        with self.assertRaises(SQLPlaceholderError):
            qmark_to_format("SELECT 1", 1)

    def test_invalid_parameter_count_is_rejected(self):
        with self.assertRaises(ValueError):
            qmark_to_format("SELECT 1", -1)


if __name__ == "__main__":
    unittest.main()
