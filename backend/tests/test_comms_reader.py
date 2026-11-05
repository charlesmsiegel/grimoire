"""032 first slice: reading a routed 008 PC intent as a message, fail-closed."""

from __future__ import annotations

import pytest

from grimoire.comms import Message, TransmissionError, Unread, read_message
from grimoire.intent import Intent, RoutingContext, Utterance, route

from comms_fixtures import EMMA, GUARD_1, INES, NAMES, actor

CONTEXT = RoutingContext(active_pc="ines", can_direct=True)


def read(text: str, *, origin: str = "keyboard", names=None, sender=INES) -> Message | Unread:
    utterance = Utterance(text, origin=origin, confidence=0.97 if origin == "voice" else None)
    return read_message(route(utterance, CONTEXT), sender, NAMES if names is None else names, message_id="m-1")


def message(channel: str, text: str, addressees=(EMMA,)) -> Message:
    return Message("m-1", INES, channel, text, addressees)


@pytest.mark.parametrize("text, expected", [
    ("I tell Emma telepathically the guards are listening", message("telepathy", "the guards are listening")),
    ("I telepathically tell Emma that the guards are listening", message("telepathy", "the guards are listening")),
    ("I mentally tell emma: the guards are listening", message("telepathy", "the guards are listening")),
    ("I tell Emma, telepathically, that the guards are listening",
     message("telepathy", "the guards are listening")),
    ("I whisper to Emma, “the guards are listening”", message("whisper", "the guards are listening")),
    ("I whisper to Emma that the guards are listening", message("whisper", "the guards are listening")),
    ("I whisper to the first guard, \"Open the gate.\"", message("whisper", "Open the gate.", (GUARD_1,))),
    ("I tell Emma telepathically: the guards are listening", message("telepathy", "the guards are listening")),
    ("I whisper to Emma, \"Run, and don't look back; they know.\"",
     message("whisper", "Run, and don't look back; they know.")),
    ("I whisper to Emma that we leave at 10:30", message("whisper", "we leave at 10:30")),
    ("Good evening, everyone.", message("speech", "Good evening, everyone.", ())),
    ("Good evening, Emma.", message("speech", "Good evening, Emma.", ())),
    ("Emma, run!", message("speech", "Emma, run!", ())),
    ("\"Good evening, everyone.\"", message("speech", "Good evening, everyone.", ())),
])
def test_directed_forms_and_plain_speech(text, expected):
    assert read(text) == expected


@pytest.mark.parametrize("text", [
    "I tell Emma telepathically the guards are listening",
    "I whisper to Emma that the guards are listening",
    "Good evening, everyone.",
    "Grimoire, open settings",
    "I tell Emma, whispering, that we should run",
    "\"Run,\" I think at Emma.",
])
def test_voice_origin_never_changes_the_reading(text):
    """A microphone is how the player spoke to the app, not how the PC spoke in the fiction."""
    assert read(text, origin="voice") == read(text, origin="keyboard")


@pytest.mark.parametrize("text", [
    "Grimoire, open settings", "open settings", "director: the guards hear everything", "",
])
def test_commands_and_non_pc_lanes_are_never_dialogue(text):
    assert read(text) == Unread("not_dialogue")


@pytest.mark.parametrize("text, reason", [
    # an action is never read as speech the room hears
    ("I tell Emma that dinner is ready", "indirect_speech"),
    ("I say to the first guard, \"Open the gate.\"", "indirect_speech"),
    ("I tell Emma, out of earshot of the guards, that we leave at midnight", "indirect_speech"),
    ("I tell Emma, mind to mind, the guards are listening", "indirect_speech"),
    ("I tell Emma silently that we should run", "indirect_speech"),
    ("I say to Emma, so only she can hear, that we should run", "indirect_speech"),
    ("I tell Emma to run", "indirect_speech"),
    # a privacy marker in an action that is not the private form is never public speech
    ("I lean in and murmur to Emma that we should run", "unresolved_private"),
    ("I mouth to Emma that we should run", "unresolved_private"),
    # spoken words mixed with narration, or aimed at a named actor
    ("\"The guards are listening,\" I whisper to Emma", "unresolved_private"),
    ("\"We leave at midnight,\" I tell Emma.", "mixed_narration"),
    ("\"Run,\" I think at Emma.", "mixed_narration"),
    ("Run, I think at Emma.", "mixed_narration"),
    ("Run, I sign towards emma", "unresolved_private"),
    ("tell Emma telepathically that the guards are listening", "unresolved_private"),
    ("Run, I mouth.", "unresolved_private"),
    ("we leave at midnight, I add under my breath", "unresolved_private"),
    ("we leave at midnight, in a low voice", "unresolved_private"),
    # the words of a private message must read as words
    ("I whisper to Emma. The guards are listening", "unclear_content"),
    ("I whisper to Emma in a low voice the guards are listening", "unclear_content"),
    ("I whisper to Emma silently the guards are listening", "unclear_content"),
    ("I whisper to Emma to run", "unclear_content"),
    ("I whisper to Emma, \"run\" and then I leave", "unclear_content"),
    ("I whisper to Emma: ...the guards", "unclear_content"),
    ("I whisper a prayer", "unresolved_private"),
    # bare "whisper" names no listener
    ("I whisper the first guard is asleep to Emma", "unresolved_private"),
    ("I whisper Emma is a traitor", "unresolved_private"),
    # a possessive name, or a head, ear or mind, is aimed at someone
    ("In Emma's head: the guards are listening", "unresolved_private"),
    ("Into Emma's mind: the guards are listening", "unresolved_private"),
    ("The guards are listening, I send to Emma's mind", "unresolved_private"),
    ("For Emma's ears only: the guards are listening", "unresolved_private"),
    ("In Emma's ear: the guards are listening", "unresolved_private"),
    ("The guards are listening, I send to Emma's thoughts", "unresolved_private"),
    ("The guards are listening, I send to Emma's", "mixed_narration"),
    ("The guards are listening, I signal into Emma", "unresolved_private"),
    ("'Run,' I tell Emma", "mixed_narration"),
    ("They said 'stop.' and left", "mixed_narration"),
    # an appositive or a second listener after the name
    ("I whisper to Emma, the first guard, that we run", "unclear_content"),
    ("I whisper to Emma, Spy and I must run", "ambiguous_addressee"),
    ("I whisper to Emma we know, that we run", "unknown_addressee"),
    ("I draw my sword", "not_directed"),
    ("I whisper to Emma and the first guard that we should run", "ambiguous_addressee"),
    ("I whisper to Emma plus Spy that we should run", "ambiguous_addressee"),
    ("I whisper to Emma as well as Spy that we should run", "ambiguous_addressee"),
    ("I tell Emma telepathically & Spy the plan", "ambiguous_addressee"),
    ("I tell her telepathically that we should run", "unknown_addressee"),
    ("I whisper to Emmy that we should run", "unknown_addressee"),
    ("I whisper to Emma Rose that we should run", "unknown_addressee"),
    ("I whisper to Emma telepathically that we should run", "conflicting_channel"),
    ("I telepathically whisper to Emma that we should run", "conflicting_channel"),
    ("I tell Emma telepathically", "no_content"),
    ("I whisper to Emma that", "no_content"),
    ("I whisper to Emma, \"\"", "no_content"),
    # stage directions in a speech line, and names that end at brackets or dashes
    ("(mindspeech) Emma, the guards are listening", "unresolved_private"),
    ("(so the guards can't hear) Emma, run", "unresolved_private"),
    ("(barely audible) Emma, run", "unresolved_private"),
    ("(over comms) Emma, the guards are listening", "unresolved_private"),
    ("[to Emma] run", "mixed_narration"),
    ("*sidles up to Emma* run", "unresolved_private"),
    ("Run, I say to Emma)", "mixed_narration"),
    ("To Emma\u2014run", "mixed_narration"),
    ("For Emma\u2026 run", "mixed_narration"),
    # spoken words that ask for privacy
    ("Psst, Emma, the guards are listening", "unresolved_private"),
    ("Emma, just between us, the guards are listening", "unresolved_private"),
    ("Keep this between you and me, Emma: run", "unresolved_private"),
    # more words after a name: the name may go on, so the listener is not known
    ("I whisper to the first guard captain that Emma is the thief", "unknown_addressee"),
    ("I whisper to Emma again that the guards are listening", "unknown_addressee"),
    ("I whisper to Emma's friend that the guards are listening", "unclear_content"),
    ("I whisper to Emma now: run", "unknown_addressee"),
    ("I telepathically tell Emma the guards are listening", "unknown_addressee"),
    # narration after the words is never sent as the sender's words
    ("I whisper to Emma that the guards are listening. Then I shout to the guards that she is a traitor.",
     "unclear_content"),
    ("I tell Emma telepathically that the guards are listening, then I wink at the first guard",
     "unclear_content"),
    ("I whisper to Emma: run; I draw my sword", "unclear_content"),
    ("I whisper to Emma that we run and I follow", "unclear_content"),
    # chat whisper conventions and "only X hears" are not speech to the room
    ("/w Emma the guards are corrupt", "mixed_narration"),
    ("/tell Emma the guards are corrupt", "mixed_narration"),
    ("@Emma the guards are corrupt", "mixed_narration"),
    ("DM Emma: the guards are corrupt", "unresolved_private"),
    ("pm Emma: the guards are corrupt", "unresolved_private"),
    ("So only Emma hears: the guards are corrupt", "unresolved_private"),
    ("Emma only: the guards are corrupt", "unresolved_private"),
    ("Emma only, the guards are corrupt", "unresolved_private"),
    ("Out of the guards' hearing: Emma, they are corrupt", "unresolved_private"),
    ("Where the guards can't hear, Emma, run", "unresolved_private"),
    ("Through our link: Emma, the guards are corrupt", "unresolved_private"),
    ("Over the bond, Emma, the guards are corrupt", "unresolved_private"),
    ("Emma -> the guards are corrupt", "mixed_narration"),
    # a speech tag is narration, quoted or not
    ("Run, I tell Emma.", "mixed_narration"),
    ("Run, Emma! I yell.", "mixed_narration"),
    ("Run, Emma, I say urgently.", "mixed_narration"),
    ("Run, Ines says.", "mixed_narration"),
    ("\u00abRun,\u00bb I tell Emma.", "mixed_narration"),
    # unquoted words of a private message are one plain clause; anything more goes in quotes
    ("I whisper to Emma: run, and wink at Spy", "unclear_content"),
    ("I whisper to Emma: run *wink*", "unclear_content"),
    ("I whisper to Emma: run (and wink)", "unclear_content"),
    ("I whisper to Emma that we run; she nods", "unclear_content"),
    ("I whisper to Emma that the guards are corrupt, glancing at the door", "unclear_content"),
    ("I tell Emma telepathically the guards are listening, then shout it to the first guard", "unclear_content"),
    ("I tell Emma telepathically the guards are listening then shout it", "unclear_content"),
    ("I whisper to Emma, my old friend: the guards are corrupt", "unclear_content"),
    ("I whisper to Emma that we run \u2014 she nods", "unclear_content"),
    # hearing, voice and ears in any word order mark a line as meant for fewer ears
    ("Emma, so the guards don't hear, they're corrupt", "unresolved_private"),
    ("Emma so the guards don't hear they're corrupt", "unresolved_private"),
    ("Emma, the guards shouldn't hear this, but they're corrupt", "unresolved_private"),
    ("Emma, the guards mustn't hear this, but they're corrupt", "unresolved_private"),
    ("Lowering my voice, Emma, the guards are corrupt", "unresolved_private"),
    ("Voice lowered, Emma, the guards are corrupt", "unresolved_private"),
    ("In a low tone, Emma, the guards are corrupt", "unresolved_private"),
    ("Dropping my voice, Emma, the guards are corrupt", "unresolved_private"),
    ("Emma, so no one overhears, the guards are corrupt", "unresolved_private"),
    ("Emma alone hears this. The guards are corrupt", "unresolved_private"),
    ("Emma, so nobody else hears, the guards are corrupt", "unresolved_private"),
    ("Out of the guards' ears, Emma, run", "unresolved_private"),
    ("The guards are corrupt, I breathe.", "unresolved_private"),
    # a speech tag in either order, or with "my character"
    ("Run, says Ines.", "mixed_narration"),
    ("Emma, the guards are corrupt, says Ines.", "mixed_narration"),
    ("Run, my character tells Emma.", "mixed_narration"),
    ("Emma, the guards are corrupt, I sign.", "unresolved_private"),
    # manner inside unquoted private words is not the words
    ("I whisper to Emma that we should run so the guards don't hear", "unclear_content"),
    ("I tell Emma telepathically that the guards are listening without looking at her", "unclear_content"),
    # a stage direction inside quotes is not words
    ("\"(whispers) Emma, the guards are corrupt\"", "mixed_narration"),
    ("\"*leans close* Emma, the guards are corrupt\"", "mixed_narration"),
    ("\"Emma (whispering): the guards are corrupt\"", "mixed_narration"),
    ("\"(to Emma only) the guards are corrupt\"", "mixed_narration"),
    ("'(whispering) Emma, the guards are corrupt'", "unresolved_private"),
    ("'Run, Emma, I whisper'", "unresolved_private"),
    ("I whisper to Emma, \"*winks* the guards are corrupt\"", "unclear_content"),
    ("I tell Emma telepathically \"(and to the first guard) the guards are corrupt\"", "unclear_content"),
    # more marker families
    ("In private, Emma, the guards are corrupt", "unresolved_private"),
    ("A word in private, Emma, the guards are corrupt", "unresolved_private"),
    ("Barely audible, Emma, the guards are corrupt", "unresolved_private"),
    ("Shh, Emma, the guards are corrupt", "unresolved_private"),
    ("In secret, Emma, the guards are corrupt", "unresolved_private"),
    ("Mindspeech, Emma, the guards are corrupt", "unresolved_private"),
    ("Over comms, Emma, the guards are corrupt", "unresolved_private"),
    ("On the radio, Emma, the guards are corrupt", "unresolved_private"),
    ("Via the earpiece, Emma, the guards are corrupt", "unresolved_private"),
    ("Leaning over, Emma, the guards are corrupt", "unresolved_private"),
    ("Drawing Emma close, the guards are corrupt", "unresolved_private"),
    ("Projecting my thoughts, Emma, the guards are corrupt", "unresolved_private"),
    ("Off the record, Emma, the guards are corrupt", "unresolved_private"),
    ("Furtively, Emma, the guards are corrupt", "unresolved_private"),
    ("Message Emma the guards are corrupt", "unresolved_private"),
    ("Keep this to yourself, Emma, the guards are corrupt", "unresolved_private"),
    ("In sign language, Emma, the guards are corrupt", "unresolved_private"),
    # a known name said aloud is only ever a vocative; anywhere else it is narration
    ("Ines slips Emma a scrap of cloth", "mixed_narration"),
    ("She passes Emma the guards' roster", "mixed_narration"),
    ("Hand Emma the key", "mixed_narration"),
    ("Send Emma the plan", "mixed_narration"),
    ("Thank you Emma", "mixed_narration"),
    ("Ines slips Emma a note saying the guards are corrupt", "unresolved_private"),
    ("My character slips Emma a note saying the guards are corrupt", "unresolved_private"),
    ("Signal Emma that the guards are corrupt", "unresolved_private"),
    ("Flash Emma a hand sign meaning the guards are corrupt", "unresolved_private"),
    ("Send Emma the thought the guards are corrupt", "unresolved_private"),
    ("Emma, for your eyes only, the guards are corrupt", "unresolved_private"),
    ("Emma, just us, the guards are corrupt", "unresolved_private"),
    ("Emma, only you, the guards are corrupt", "unresolved_private"),
    ("Mind message Emma the guards are corrupt", "unresolved_private"),
    ("Emma, entre nous, the guards are corrupt", "unresolved_private"),
    ("Emma, the guards are corrupt, my words unvoiced", "unresolved_private"),
    # reported words with shifted pronouns are not the words said
    ("I whisper to Emma that I love her", "unclear_content"),
    ("I tell Emma telepathically that her brother is the spy", "unclear_content"),
    ("I whisper to Emma that she should run", "unclear_content"),
    # single quotes are also apostrophes, so a single-quoted line gets every unquoted rule
    ("'Run, Emma, I murmur'", "unresolved_private"),
    ("'The guards are corrupt, I mutter to Emma'", "unresolved_private"),
    ("'Run, I say to Emma'", "mixed_narration"),
    ("'Cause only you can hear this, Emma, the guards are listenin'", "unresolved_private"),
    # more confidentiality phrasings
    ("Between you and I, Emma, the guards are corrupt.", "unresolved_private"),
    ("Don't tell anyone, Emma, but the guards are corrupt.", "unresolved_private"),
    ("Tell no one, Emma, the guards are corrupt.", "unresolved_private"),
    ("Not a word to anyone, Emma, the guards are corrupt.", "unresolved_private"),
    ("Emma, this is confidential, the guards are corrupt.", "unresolved_private"),
    ("Just for you, Emma, the guards are corrupt.", "unresolved_private"),
    ("Emma, keep this under wraps, the guards are corrupt.", "unresolved_private"),
    ("Leaning toward her, Emma, the guards are corrupt.", "unresolved_private"),
    # more narration after private words
    ("I whisper to Emma that we run & I yell", "unclear_content"),
    ("I whisper to Emma that we run!!I yell at the first guard", "unclear_content"),
    ("I whisper to Emma that we run\"", "unclear_content"),
    # a known name, or a second "that", in unquoted private words may be another listener's message
    ("I whisper to the first guard that Emma is the thief and to Emma that we leave now", "ambiguous_addressee"),
    ("I whisper to Emma that we run and to the first guard that we stay", "ambiguous_addressee"),
    ("I tell Emma telepathically that we run and the first guard that we stay", "ambiguous_addressee"),
    ("I whisper to Emma that the vault code is 7 and to the second guard that it is 9", "unclear_content"),
    ("I whisper to the first guard that Emma is the thief", "ambiguous_addressee"),
    ("I whisper to Emma that we run and to the captain that we stay", "unclear_content"),
    # more quiet-voice and confidentiality phrasings, and a trailing tag with any verb
    ("Ssh, Emma, the guards are corrupt.", "unresolved_private"),
    ("Sshh, Emma, the guards are corrupt.", "unresolved_private"),
    ("Shush, Emma, the guards are corrupt.", "unresolved_private"),
    ("Pst, Emma, the guards are corrupt.", "unresolved_private"),
    ("Keep it down, Emma, the guards are corrupt.", "unresolved_private"),
    ("Emma, the guards are corrupt. Keep it down.", "unresolved_private"),
    ("Run, Emma, I mumble.", "unresolved_private"),
    ("The guards are corrupt, I confide.", "unresolved_private"),
    ("Run, Emma, I gasp.", "mixed_narration"),
    ("No one can know, Emma, but the guards are corrupt.", "unresolved_private"),
    ("Don't let the guards know, Emma, they are corrupt.", "unresolved_private"),
    ("Emma, don't repeat this, the guards are corrupt.", "unresolved_private"),
    ("Emma, keep this from the guards, they are corrupt.", "unresolved_private"),
    ("Emma, my lips are sealed, but the guards are corrupt.", "unresolved_private"),
    ("With my back to the guards, Emma, they are corrupt.", "unresolved_private"),
    ("Turning away from the guards, Emma, they are corrupt.", "unresolved_private"),
    ("Emma, a word? The guards are corrupt.", "unresolved_private"),
    ("Via link, Emma, the guards are corrupt.", "unresolved_private"),
    # markdown italics, tildes and angle brackets are stage directions too, quoted or not
    ("_whispering_ The guards are corrupt.", "unresolved_private"),
    ("_leans in close_ The guards are corrupt.", "unresolved_private"),
    ("The guards are corrupt, Emma. _sotto voce_", "unresolved_private"),
    ("_shrugs_ The guards are corrupt.", "mixed_narration"),
    ("\"_whispers_ Emma, the guards are corrupt\"", "mixed_narration"),
    ("\"<whispers> Emma, the guards are corrupt\"", "mixed_narration"),
    ("\"~whispers~ Emma, the guards are corrupt\"", "mixed_narration"),
    ("\"Run,' I whisper to Emma, 'now\"", "mixed_narration"),
    ("I whisper to Emma that we run -- glancing at the door", "unclear_content"),
    ("I whisper to Emma that we run _winks_", "unclear_content"),
    # more confidentiality phrasings
    ("Emma, between me and you, the guards are corrupt.", "unresolved_private"),
    ("Nobody can know, Emma, but the guards are corrupt.", "unresolved_private"),
    ("Emma, you can't tell anyone, but the guards are corrupt.", "unresolved_private"),
    ("Emma, promise you won't tell anyone, but the guards are corrupt.", "unresolved_private"),
    ("Don't mention this to anyone, Emma, but the guards are corrupt.", "unresolved_private"),
    ("Emma, no one needs to know, but the guards are corrupt.", "unresolved_private"),
    ("Not a soul can know, Emma, the guards are corrupt.", "unresolved_private"),
    ("Emma, this is just for us. The guards are corrupt.", "unresolved_private"),
    ("Conspiratorially, Emma, the guards are corrupt.", "unresolved_private"),
    ("Stepping away from the guards, Emma, they are corrupt.", "unresolved_private"),
    ("Out of the guards' sight, Emma, they are corrupt.", "unresolved_private"),
    # brackets of any script, compatibility forms and invisible characters hide nothing
    ("\"\uff08whispers\uff09 Emma, the guards are corrupt\"", "mixed_narration"),
    ("\"\u3010whispers\u3011 Emma, the guards are corrupt\"", "mixed_narration"),
    ("\"\u27e8whispers\u27e9 Emma, the guards are corrupt\"", "mixed_narration"),
    ("\"\uff0aleans close\uff0a Emma, the guards are corrupt\"", "mixed_narration"),
    ("\"\u2217leans close\u2217 Emma, the guards are corrupt\"", "mixed_narration"),
    ("\"Run,\uff02 I whisper to Emma, \uff02now\"", "mixed_narration"),
    ("\"Run,\u301e I whisper to Emma, \u301dnow\"", "mixed_narration"),
    ("I whisper to Emma, \"\uff08and to the first guard\uff09 the guards are corrupt\"", "unclear_content"),
    ("\uff37\uff48\uff49\uff53\uff50\uff45\uff52\uff49\uff4e\uff47\uff0c Emma, the guards are corrupt",
     "mixed_narration"),
    ("Whis\u00adpering, Emma, the guards are corrupt", "unresolved_private"),
    ("I whisper to Emma that we run\uff1bI draw my sword", "unclear_content"),
    ("I whisper to Emma that we run...and smile", "unclear_content"),
    ("Run, Emma, I gasp quickly.", "mixed_narration"),
    # more confidentiality phrasings
    ("Keep mum, Emma, the guards are corrupt.", "unresolved_private"),
    ("Emma, nobody must find out, but the guards are corrupt.", "unresolved_private"),
    ("Hide this from the guards, Emma, they are corrupt.", "unresolved_private"),
    ("Emma, the guards can't know, but they are corrupt.", "unresolved_private"),
    ("Emma, so the guards don't notice, they are corrupt.", "unresolved_private"),
    # unspaced inner quotes, invisible joiners, emoji and a second listener
    ("\"Run,'I whisper to Emma,'now\"", "mixed_narration"),
    ("Keep your voi\u034fce down, Emma, the guards are corrupt.", "unresolved_private"),
    ("Emma, the guards are listening \U0001f92b", "mixed_narration"),
    ("I whisper to Emma that we run and to the guards we stay", "unclear_content"),
    ("Emma, in utmost secrecy, the guards are corrupt.", "unresolved_private"),
    ("Emma, in complete privacy, the guards are corrupt.", "unresolved_private"),
    ("Emma, discreet as ever, the guards are corrupt.", "unresolved_private"),
    ("I whisper to Emma that we run + to the guards we stay", "unclear_content"),
    ("I whisper to Emma that we run or to the guards we stay", "unclear_content"),
    ("I whisper to Emma that we run & we stay", "unclear_content"),
    # a short aside that says how or where is refused by its shape, whatever its words
    ("Emma, in silence, the guards are corrupt.", "unresolved_private"),
    ("Emma, by sending spell, the guards are corrupt.", "unresolved_private"),
    ("Emma, behind closed doors, the guards are corrupt.", "unresolved_private"),
    ("Emma, magically, the guards are corrupt.", "unresolved_private"),
    ("In seclusion. Emma, the guards are corrupt.", "unresolved_private"),
    ("Emma, the guards are corrupt, off the books.", "unresolved_private"),
    ("Emma, one-on-one, the guards are corrupt.", "unresolved_private"),
    ("Emma, nobody but you, the guards are corrupt.", "unresolved_private"),
    ("Emma, between you & me, the guards are corrupt.", "unresolved_private"),
    ("I whisper to Emma that we run yet to the guards we stay", "unclear_content"),
    ("I whisper to Emma that we run so to the guards we stay", "unclear_content"),
])
def test_refusals_transmit_nothing(text, reason):
    assert read(text) == Unread(reason)


def test_two_actors_matching_one_name_is_never_guessed():
    names = {"Emma": EMMA, "Emma Rose": actor("emma-rose")}
    assert read("I whisper to Emma Rose that we should run", names=names) == Unread("ambiguous_addressee")
    assert read("I whisper to Emma that we should run", names=names) == message("whisper", "we should run")
    shared = {"Emma": EMMA, "emma": actor("emma-2")}
    assert read("I whisper to Emma that we should run", names=shared) == Unread("ambiguous_addressee")
    alias = {"Emma": EMMA, "Emma Rose": EMMA}
    assert read("I whisper to Emma Rose that we should run", names=alias) == message("whisper", "we should run")


def test_a_name_must_end_at_a_word_boundary():
    assert read("I whisper to Emmanuel that we run", names={"Emma": EMMA}) == Unread("unknown_addressee")
    assert read("I whisper to EMMA, that we run") == message("whisper", "we run")
    kelvin = {"\u212aira": actor("kira")}  # KELVIN SIGN K: case-folds like K
    assert read("I whisper to kira that we run", names=kelvin) == message("whisper", "we run", (actor("kira"),))


def test_names_whose_case_fold_changes_length_are_still_names():
    """"Straße" folds to "strasse": capitalisation never changes who a name means."""
    street, guard = actor("strasse"), actor("guard-strasse")
    both = {"Straße": street, "STRASSE": guard}
    assert read("I whisper to STRASSE that we run", names=both) == Unread("ambiguous_addressee")
    assert read("I whisper to straße that we run", names=both) == Unread("ambiguous_addressee")
    one = {"Straße": street}
    assert read("I whisper to STRASSE that we run", names=one) == message("whisper", "we run", (street,))
    assert read("Run, I send it to STRASSE", names=one) == Unread("mixed_narration")
    assert read("Run, I send it to Straße", names=one) == Unread("mixed_narration")
    assert read("STRASSE, run!", names=one) == message("speech", "STRASSE, run!", ())
    assert read("I whisper to Strasser that we run", names=one) == Unread("unknown_addressee")


def test_canonically_equal_spellings_of_a_name_are_the_same_name():
    decomposed = {"E\u0301mma": EMMA}
    assert read("Run, I send it to \u00c9mma", names=decomposed) == Unread("mixed_narration")
    assert read("I whisper to \u00c9mma that we run", names=decomposed) == message("whisper", "we run")
    composed = {"\u00c9mma": EMMA}
    assert read("Run, I send it to E\u0301mma", names=composed) == Unread("mixed_narration")


def test_quoted_speech_keeps_its_words_even_with_privacy_words_inside():
    assert read("\"I'll whisper it to you later.\"") == message("speech", "I'll whisper it to you later.", ())
    assert read("'Good evening, everyone.'", ) == message("speech", "Good evening, everyone.", ())


def test_overlong_text_is_refused_before_any_parsing():
    import time
    for text in ("\u65e5\u672c\u8a9e" * 20000, "_" * 60000, "whisper " * 4000):
        intent = route(Utterance(text), CONTEXT)
        if intent.kind not in ("pc_speech", "pc_action"):
            continue
        started = time.perf_counter()
        with pytest.raises(TransmissionError) as caught:
            read_message(intent, INES, NAMES, message_id="m-1")
        assert caught.value.reason == "too_long" and time.perf_counter() - started < 1


def test_a_long_word_is_read_in_linear_time():
    import time
    started = time.perf_counter()
    assert read("\u65e5\u672c\u8a9e" * 6600) == message("speech", "\u65e5\u672c\u8a9e" * 6600, ())
    assert time.perf_counter() - started < 1


def test_many_names_and_many_prepositions_are_read_in_linear_time():
    import time
    crowd = {f"guest {n}": actor(f"guest-{n}") for n in range(2000)}
    started = time.perf_counter()
    assert read("to " * 6666, names=crowd) == message("speech", ("to " * 6666).strip(), ())
    assert time.perf_counter() - started < 1


def test_many_vocatives_and_shared_first_words_are_read_quickly():
    import time
    started = time.perf_counter()
    read("'" + "Emma, " * 3300 + "'")
    read("'" + "Emma, run " * 1900 + "'")
    read("the " * 4900, names={f"the guest {n}": actor(f"guest-{n}") for n in range(2000)})
    assert time.perf_counter() - started < 2


@pytest.mark.parametrize("text", ["a_" * 9999, "I " + "a_" * 9990, "a_" * 4999 + "Emma"])
def test_words_joined_by_underscores_are_read_quickly(text):
    import time
    started = time.perf_counter()
    read(text)
    assert time.perf_counter() - started < 1


def test_a_fold_that_grows_the_text_is_not_too_long():
    """NFKC expands an ellipsis to three dots; the length limit is on the typed words."""
    text = '"' + "Wait\u2026 " * 3330 + 'go"'
    result = read(text)
    assert result == message("speech", text[1:-1], ())


def test_the_words_kept_are_the_ones_typed():
    """Checks run on the folded form too, but full-width letters are stored as typed."""
    assert read("\uff27\uff4f\uff4f\uff44 evening.") == message("speech", "\uff27\uff4f\uff4f\uff44 evening.", ())


def test_a_reentrant_voice_candidate_loses_its_quotes():
    utterance = Utterance("\"Grimoire, open settings\"", origin="voice", confidence=0.5)
    (speech,) = [c for c in route(utterance, CONTEXT).candidates if c.kind == "pc_speech"]
    assert speech.reason == "quoted" and speech.text.startswith('"')
    assert read_message(speech, INES, NAMES, message_id="m-1") == message("speech", "Grimoire, open settings", ())


def test_the_routed_actor_must_be_the_sender():
    assert read("I whisper to Emma that we should run", sender=actor("emma-2")) == Unread("wrong_speaker")


def test_malformed_arguments_raise_text_free_errors():
    intent = route(Utterance("I tell Emma that we should run"), CONTEXT)
    for call, reason in [
        (lambda: read_message("I tell Emma", INES, NAMES, message_id="m-1"), "invalid_record"),
        (lambda: read_message(intent, str(INES), NAMES, message_id="m-1"), "invalid_record"),
        (lambda: read_message(intent, INES, [("Emma", EMMA)], message_id="m-1"), "invalid_record"),
        (lambda: read_message(intent, INES, {"Emma": str(EMMA)}, message_id="m-1"), "invalid_record"),
        (lambda: read_message(intent, INES, {"  ": EMMA}, message_id="m-1"), "invalid_record"),
        (lambda: read_message(intent, INES, {"E" * 201: EMMA}, message_id="m-1"), "invalid_record"),
        (lambda: read_message(intent, INES, {"Emma": actor("emma", campaign="c2")}, message_id="m-1"),
         "campaign_mismatch"),
        (lambda: read_message(intent, INES, NAMES, message_id="Bad Id"), "invalid_record"),
        (lambda: read_message(route(Utterance("open settings"), CONTEXT), INES, NAMES, message_id="Bad Id"),
         "invalid_record"),
        (lambda: read_message(intent, INES, NAMES, message_id="m" * 110), "invalid_record"),
        (lambda: read_message(route(Utterance('"' + "x" * 20_001 + '"'), CONTEXT), INES, NAMES, message_id="m-1"),
         "too_long"),
        (lambda: read_message(route(Utterance('"hi \ud800"'), CONTEXT), INES, NAMES, message_id="m-1"),
         "invalid_record"),
    ]:
        with pytest.raises(TransmissionError) as caught:
            call()
        assert caught.value.reason == reason and caught.value.__context__ is None


def test_unread_reasons_are_closed():
    with pytest.raises(ValueError):
        Unread("speech")


def test_only_the_two_pc_lanes_are_read():
    for kind in ("director_instruction", "pc_action"):
        intent = Intent(kind, "I tell Emma that we should run", "explicit_mode", "feedback",
                        actor_id="ines" if kind == "pc_action" else None)
        expected = Unread("not_dialogue") if kind != "pc_action" else Unread("indirect_speech")
        assert read_message(intent, INES, NAMES, message_id="m-1") == expected


def test_thought_is_never_transmitted():
    assert read("I think the guards are listening") == Unread("not_directed")
