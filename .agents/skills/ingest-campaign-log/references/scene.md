# Scene payload and ingestion

   d. Write the scene to a JSON file and ingest it:
      ```json
      {
        "key": "file1-scene03",
        "title": "The Reckoning",
        "date": "1818-05-15",
        "location": "the-counting-house",
        "new_characters": [{"name": "Mara", "personality": "wary, precise"}],
        "new_locations": [{"name": "The Counting House", "notes": "Guild meeting place."}],
        "characters": [{"kind": "pcs", "id": "winifred"}, {"kind": "characters", "id": "mara"}],
        "turns": [
          {"role": "assistant", "speaker": null, "content": "*The study is silent.*"},
          {"role": "assistant", "speaker": "Mara", "content": "\"I didn't ask to come here.\""},
          {"role": "user", "speaker": "Winifred", "content": "\"Neither did I, once.\""}
        ]
      }
      ```
      ```bash
      $PY backend/scripts/ingest_scene.py ingest --campaign <cid> --input scene.json
      ```
      This creates the scene, seats the cast, sets location/date, runs the real absorb LLM call
      through the campaign's resolved `absorb` route, role, provider and fallback, and auto-applies every edit it proposes.
      It's a real API call and real spend — there is no dry-run mode.
