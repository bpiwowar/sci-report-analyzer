from sci_report_analyzer.authors import fold_name, name_key, natural_order, same_author


def test_natural_order():
    assert natural_order("LEFEVRE Hélène") == "Hélène LEFEVRE"
    assert natural_order("KOSTAKIS Yannis") == "Yannis KOSTAKIS"
    assert natural_order("DE LA FONTAINE Jean") == "Jean DE LA FONTAINE"
    assert natural_order("Marchetti, Julien") == "Julien Marchetti"
    assert natural_order("Julien Marchetti") == "Julien Marchetti"
    assert natural_order("TANABE") == "TANABE"  # all capitals: nothing to reorder
    assert natural_order("J. Doe") == "J. Doe"
    assert natural_order("R.L. Doe") == "R.L. Doe"  # (initials, not a surname)


def test_surname_first_names_match_author_lists():
    assert name_key("LEFEVRE Hélène") == ("lefevre", "h")
    assert same_author("LEFEVRE Hélène", "Hélène Lefèvre")
    assert fold_name("LEFEVRE Hélène") == fold_name("Hélène Lefèvre")


def test_former_phd_student():
    from sci_report_analyzer.pubview import PeopleIndex, StudentNames

    st = StudentNames("Ann Lee", {fold_name("Ann Lee")}, ["Ann Lee"], set(), "PhD student", 2018)
    people = PeopleIndex(set(), [], set(), [st])
    authors = ["Ann Lee", "Bob Smith"]
    assert people.marks(authors, None, year=2020)[0] == ["student", None]
    assert people.marks(authors, None, year=2021)[0] == ["former", None]
    assert people.marks(authors, None)[0] == ["student", None]  # no year: still a student
