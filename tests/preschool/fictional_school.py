"""A fictional school for tests. Real school details live only in the owner's private household.json."""
import json

SCHOOL = {
    "name": "Maple Hill Preschool",
    "search_name": "Maple Hill Preschool",
    "short_name": "MHP",
    "class_name": "Sprouts",
    "class_plural": "Sprouts",
    "organization_name": "Maple Hill Community Association",
    "organization_short": "MHCA",
    "news_name": "MapleNews",
    "source_hosts": ["maple-hill.example", "www.maple-hill.example",
                     "maplenews.example", "www.maplenews.example",
                     "maplehillpreschool.example", "www.maplehillpreschool.example"],
    "publishable_hosts": ["maple-hill.example", "www.maple-hill.example",
                          "maplehillpreschool.example", "www.maplehillpreschool.example"],
    "calendar_sheet_id": "SyntheticCalendarSheet0123456789",
    "resource_words": ["sproutly"],
}


def write_school(path, school=SCHOOL):
    file = path / "household.json"
    data = json.loads(file.read_text()) if file.exists() else {}
    data["school"] = school
    file.write_text(json.dumps(data))
