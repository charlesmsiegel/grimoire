"""Synthetic command phrase tables shared by the intent grammar and parity tests."""

NAV_CASES = [
    ("Open the inventory", "inventory"),
    ("show my journal", "journal"),
    ("go to settings", "settings"),
    ("navigate to the map", "map"),
    ("open the scene list", "scenes"),
    ("show scenes", "scenes"),
    ("show characters", "characters"),
    ("show character list", "characters"),
]

SIMPLE_CASES = [
    ("pause", "pause"),
    ("Please pause.", "pause"),
    ("resume", "resume"),
    ("continue", "resume"),
    ("unpause", "resume"),
    ("stop", "stop"),
    ("stop audio", "stop"),
    ("stop talking", "stop"),
    ("stop generating", "stop"),
    ("be quiet", "stop"),
    ("cancel", "cancel"),
    ("never mind", "cancel"),
    ("open the review queue", "review"),
    ("show review", "review"),
    ("review changes", "review"),
    ("undo", "undo"),
    ("undo that", "undo"),
    ("reroll", "reroll"),
    ("regenerate", "reroll"),
    ("try again", "reroll"),
    ("edit the draft", "edit_draft"),
    ("edit draft", "edit_draft"),
    ("edit my reply", "edit_draft"),
    ("delete the scene", "delete_scene"),
    ("remove this scene", "delete_scene"),
    ("erase scene", "delete_scene"),
    ("DELETE THE SCENE!", "delete_scene"),
]

CHOOSE_CASES = [
    ("choose option two", "2"),
    ("Choose option 2", "2"),
    ("pick the second option", "2"),
    ("option 2", "2"),
    ("select option 2", "2"),
    ("the second option", "2"),
    ("option 9", "9"),
    ("the ninth option", "9"),
    ("pick option one", "1"),
]

CHARACTER_CASES = [
    ("Open character Mara", "Mara"),
    ("show the character Mara Vell.", "Mara Vell"),
    ("Open Mara’s sheet", "Mara"),
    ("show Mara's sheet", "Mara"),
]
