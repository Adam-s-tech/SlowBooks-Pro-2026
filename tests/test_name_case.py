"""Tests for readable names from QuickBooks' all-caps habit.

The contract these lock down:

* a name with no lowercase letter at all is re-cased for readability;
* a name that already has mixed case is returned byte-for-byte unchanged,
  because we cannot tell intent from typo and must not corrupt ``ConEdison``;
* acronyms, legal forms and product codes keep their own casing;
* a domain is cased as a domain (``Amazon.com``, never ``Amazon.Com``);
* the literal ``\\n`` QuickBooks sometimes stores is dropped.

Fixtures here are public company and product names. Please do not paste real
customer names into this file.
"""

from app.services.name_case import normalize_name


class TestAllCapsIsRetitled:
    def test_legal_form_keeps_its_case(self):
        assert normalize_name("ACME TOOLING, INC.") == "ACME Tooling, Inc."
        assert normalize_name("SAMPLE HOLDINGS INC") == "Sample Holdings Inc"
        assert normalize_name("160 PARKING CORP") == "160 Parking Corp"
        assert normalize_name("5421 EQUITIES CO") == "5421 Equities Co"

    def test_acronym_survives(self):
        assert (
            normalize_name("NYC DEPARTMENT OF FINANCE") == "NYC Department of Finance"
        )
        assert normalize_name("NYC DEPT OF FINCANCE") == "NYC Dept of Fincance"
        assert normalize_name("HSBC BUSINESS") == "HSBC Business"

    def test_initialism_in_trading_name_survives(self):
        assert normalize_name("JBC INTERNATIONAL, INC.") == "JBC International, Inc."
        assert normalize_name("JBC") == "JBC"

    def test_initialism_keeps_caps_beside_ordinary_words(self):
        # An initialism is not an ordinary shouted word, so it keeps its caps
        # while the words around it are retitled.
        assert normalize_name("ACME TOOLING, INC.") == "ACME Tooling, Inc."
        assert normalize_name("ACME") == "ACME"

    def test_domains_are_cased_as_domains(self):
        # Trailing labels lower-case: "Amazon.com", never "Amazon.Com". The
        # leading label is the exception for known acronyms: "WWW", never "Www".
        assert normalize_name("AMAZON.COM") == "Amazon.com"
        assert normalize_name("WWW.EXAMPLE.COM") == "WWW.Example.com"
        assert normalize_name("WWW.1AND1.COM") == "WWW.1AND1.com"

    def test_apostrophe_makes_it_a_company_name_not_a_domain(self):
        # No real host contains an apostrophe, so this is a company name that
        # happens to end in a suffix, and is cased word by word: "Smith's.Com".
        assert normalize_name("SMITH'S.COM") == "Smith's.Com"

    def test_product_code_survives(self):
        assert normalize_name("MSC-3800") == "MSC-3800"
        assert normalize_name("22QT") == "22QT"
        assert normalize_name("TC-250") == "TC-250"

    def test_sub_accounts_title_each_side(self):
        assert (
            normalize_name("AUTOMOBILE EXPENSE:GASOLINE")
            == "Automobile Expense:Gasoline"
        )

    def test_separators_and_apostrophes_preserved(self):
        assert normalize_name("PUBLIC, JOHN/JANE") == "Public, John/Jane"
        assert normalize_name("O'BRIEN & SONS") == "O'Brien & Sons"
        assert normalize_name("A-1 AIRPORT LIMOUSINE") == "A-1 Airport Limousine"
        assert normalize_name("1 & 1 INTERNET") == "1 & 1 Internet"

    def test_numbers_still_title_the_rest(self):
        assert normalize_name("25 STRATFORD ROAD HDFC") == "25 Stratford Road HDFC"


class TestMixedCaseIsNeverTouched:
    def test_brand_names_unchanged(self):
        for name in (
            "ConEdison",
            "Linksys RV042",
            "EZPass",
            "T-Mobile",
            "GoDaddy.com",
            "Applied Minerals, Inc.",
            "AT&T (S.I. Branch)",
            "illycaffè",
        ):
            assert normalize_name(name) == name, name

    def test_normalize_is_idempotent(self):
        once = normalize_name("ACME TOOLING, INC.")
        assert normalize_name(once) == once


class TestEdgeCases:
    def test_literal_escapes_dropped(self):
        assert normalize_name("Sample Name\\n") == "Sample Name"

    def test_empty_and_none_pass_through(self):
        assert normalize_name("") == ""
        assert normalize_name(None) is None

    def test_leading_apostrophe_preserved(self):
        assert normalize_name("'SAMPLE VENDOR") == "'Sample Vendor"

    def test_formula_and_handle_prefixes_untouched(self):
        # Re-casing these would defeat the formula guard the importer strips and
        # can make a spreadsheet treat the cell as a live formula.
        for name in ("=HYPERLINK(1)", "@Acme", "+1 555 0100", "-ACME"):
            assert normalize_name(name) == name, name


class TestCommonNames:
    """Names the first word lists got wrong (2.18.1 review of #195). Words
    that are also ordinary words in business names are not acronyms, letters
    that stand for words keep their caps, and each side of a colon is a name
    of its own."""

    def test_ordinary_words_are_not_acronyms(self):
        assert normalize_name("BANK OF AMERICA") == "Bank of America"
        assert normalize_name("NEW YORK LIFE") == "New York Life"
        assert normalize_name("CHASE BANK") == "Chase Bank"
        assert normalize_name("SHELL OIL") == "Shell Oil"
        assert normalize_name("POLKA DOT BAKERY") == "Polka Dot Bakery"
        assert normalize_name("CITIBANK") == "Citibank"

    def test_na_is_national_association(self):
        assert normalize_name("WELLS FARGO BANK NA") == "Wells Fargo Bank NA"
        assert normalize_name("WELLS FARGO BANK, N.A.") == "Wells Fargo Bank, N.A."

    def test_initials_keep_their_caps(self):
        assert normalize_name("AT&T") == "AT&T"
        assert normalize_name("H&R BLOCK") == "H&R Block"
        assert normalize_name("M&T BANK") == "M&T Bank"
        assert normalize_name("ACCOUNTS RECEIVABLE A/R") == "Accounts Receivable A/R"
        assert normalize_name("C/O JOHN SMITH") == "C/O John Smith"
        assert normalize_name("J. SMITH PLUMBING") == "J. Smith Plumbing"
        assert normalize_name("D/B/A SAMPLE") == "D/B/A Sample"

    def test_common_initialisms(self):
        assert normalize_name("CVS PHARMACY") == "CVS Pharmacy"
        assert normalize_name("USAA") == "USAA"
        assert normalize_name("ABC SUPPLY CO") == "ABC Supply Co"
        assert normalize_name("SMITH DDS") == "Smith DDS"
        assert normalize_name("ABC PLUMBING NJ") == "ABC Plumbing NJ"
        assert normalize_name("PAYROLL EXPENSES:FICA") == "Payroll Expenses:FICA"
        assert normalize_name("COST OF GOODS SOLD:COGS") == "Cost of Goods Sold:COGS"

    def test_names_written_their_own_way(self):
        assert normalize_name("MCDONALD'S") == "McDonald's"
        assert normalize_name("FEDEX") == "FedEx"
        assert normalize_name("PAYPAL FEES") == "PayPal Fees"
        assert normalize_name("MACHINE SHOP") == "Machine Shop"
        assert normalize_name("ST. LOUIS BREAD CO.") == "St. Louis Bread Co."

    def test_each_side_of_a_colon_is_judged_on_its_own(self):
        # a job typed in mixed case under a customer typed in caps
        assert (
            normalize_name("BOB JONES:Kitchen remodel") == "Bob Jones:Kitchen remodel"
        )
        assert normalize_name("Bob Jones:KITCHEN") == "Bob Jones:Kitchen"
