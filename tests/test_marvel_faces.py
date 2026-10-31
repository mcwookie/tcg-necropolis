"""Renaming two-sided Marvel Champions images to the catalog's convention."""

from PIL import Image

from tcgnecro.games.marvel_champions.faces import apply, plan, write_plan

CARDS = [
    {"code": "27088", "name": "Edge of Reality", "type_code": "main_scheme"},        # plain double
    {"code": "27088a", "name": "Edge of Reality", "type_code": "main_scheme", "linked_to_code": "27088b"},
    {"code": "27088b", "name": "Edge of Reality", "type_code": "main_scheme", "hidden": True,
     "linked_to_code": "27088a"},
    {"code": "04159a", "name": "Basic Thwart Upgrade", "type_code": "upgrade", "linked_to_code": "04159b"},
    {"code": "04159b", "name": "Improved Thwart Upgrade", "type_code": "upgrade", "hidden": True},
    {"code": "01002", "name": "Black Cat", "type_code": "ally"},
]


def folder_with(tmp_path, *names):
    for name in names:
        Image.new("RGB", (6, 8)).save(tmp_path / name)
    return tmp_path


def test_plan_is_certain_only_for_a_back_whose_front_exists(tmp_path):
    folder = folder_with(tmp_path, "27088@sm.jpg", "27088b@sm.jpg", "04159b@trors.jpg", "01002@core.jpg")
    renames = {r.file: r for r in plan(folder, CARDS)}
    assert "01002@core.jpg" not in renames                          # already fine
    r = renames["27088@sm.jpg"]                                      # plain code, no front yet
    assert (r.action, r.as_front, r.certain) == ("front", "27088a@sm.jpg", False)
    r = renames["27088b@sm.jpg"]                                     # front planned above
    assert (r.action, r.as_back, r.certain) == ("back", "27088a@sm~back.jpg", True)
    r = renames["04159b@trors.jpg"]                                  # no front image: needs a look
    assert (r.action, r.as_back, r.as_front, r.certain) == ("back", "04159a@trors~back.jpg", "04159a@trors.jpg", False)


def test_plain_code_beside_an_existing_front_is_planned_as_the_back(tmp_path):
    folder = folder_with(tmp_path, "27088@sm.jpg", "27088a@sm.jpg")   # like 02017@gob (a 1B back)
    (r,) = plan(folder, CARDS)
    assert (r.file, r.action, r.as_back, r.certain) == ("27088@sm.jpg", "back", "27088a@sm~back.jpg", False)


def test_never_plans_onto_a_taken_name(tmp_path):
    folder = folder_with(tmp_path, "27088@sm.jpg", "27088a@sm.jpg", "27088a@sm~back.jpg")
    (r,) = plan(folder, CARDS)
    assert r.action == "skip" and "duplicate" in r.why


def test_apply_follows_the_edited_plan_and_never_overwrites(tmp_path):
    (tmp_path / "scans").mkdir()
    folder = folder_with(tmp_path / "scans", "27088@sm.jpg", "27088b@sm.jpg", "04159b@trors.jpg")
    plan_file = tmp_path / "plan.tsv"
    write_plan(plan(folder, CARDS), plan_file)
    text = plan_file.read_text().replace("back\t" + str(folder) + "\t04159b@trors.jpg",
                                          "front\t" + str(folder) + "\t04159b@trors.jpg")
    plan_file.write_text(text)                                       # reviewed: it's the front

    renamed, problems = apply(plan_file)
    assert renamed == 3 and problems == []
    assert sorted(p.name for p in folder.iterdir()) == ["04159a@trors.jpg", "27088a@sm.jpg", "27088a@sm~back.jpg"]

    renamed, problems = apply(plan_file)                             # running again changes nothing
    assert renamed == 0 and all("not found" in p for p in problems)

    folder_with(folder, "27088b@sm.jpg")                             # a duplicate turns up
    (r,) = plan(folder, CARDS)
    assert r.action == "skip"                                        # the plan won't touch it,
    write_plan([r], plan_file)
    plan_file.write_text(plan_file.read_text().replace("skip\t", "back\t"))
    renamed, problems = apply(plan_file)                             # nor will a hand-edited one
    assert renamed == 0 and "already exists" in problems[0]
