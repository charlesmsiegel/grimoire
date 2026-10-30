# Hands-Free Controls and Spoken Intent Routing

**Status:** first slice implemented as the headless `grimoire.intent` router (typed/fake transcripts, gated commands, target-bound confirmation); see [intent routing contract](../../intent-routing.md). No audio capture, speech recognition, UI or execution yet; milestones 2–3 remain open.

**Outcome:** Control Grimoire by voice while clearly distinguishing application commands, out-of-character direction, PC speech and narrated action.

## Scope
- Define explicit input modes and a minimal intent envelope: PC utterance, PC action, director instruction, UI command, uncertainty or reject.
- Support navigate, open character, pause, reroll, choose option, edit draft, stop audio and review operations with accessible feedback.
- Gate destructive/ambiguous commands, source edits and game effects behind the same authorization policies as mouse/keyboard.

## Milestones
1. Prototype a reliable push-to-talk command set, including cancel/undo/stop.
2. Demonstrate safe mixed 'in character' and 'control the app' utterances with clear visible mode feedback.
3. Explore context-aware mode switching and optional wake word without silent misclassification.

## Moonshot
A player can run an entire campaign hands-free—including scene setup, roleplay, steering, reviews and recovery—without sacrificing exact control or knowing a rigid command grammar.

## Proving scenario
'Open the inventory' navigates; 'I open the chest' is in-character action; 'Director, make this tense' is guidance. 'Delete the scene' requires confirmation; uncertain speech does nothing destructive.

## Independence and interfaces
This is the top-priority voice design decision. It can use typed/fake transcriptions and current UI actions before selecting speech hardware or real-time APIs.

## Open questions
- Explicit modes, wake phrases or automatic intent inference—and what is the safe fallback?
- Which voice commands can be applied instantly versus confirmed?
- How do players switch controller/PC/GM roles without frustrating mode errors?
