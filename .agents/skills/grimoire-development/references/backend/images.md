# Image storage and maintenance

Source: [assets/](../../../../../backend/src/grimoire/store/assets/), [test_image_surfaces.py](../../../../../backend/tests/test_image_surfaces.py).

- **Images live in the content-addressed store, never in a record's folder.**
  Every image write goes through the `store.assets` primitives: `put_in` /
  `put_image` ingest the bytes into `<home>/assets/image-store/`, `link_in`
  places an image already there, and either way the record keeps only a
  placement, `image-refs/<name>.json`. Copying, forking or promoting
  art moves placements and writes no bytes; a legacy file not yet migrated
  is still copied as a file, and promoting one ingests it first. A route or
  script that writes a `.png` into a record directory is a regression, and
  the surface roster in `backend/tests/test_image_surfaces.py` fails on it.
  A picture's description and its subject tags live on its object, not in the
  record's folder: a legacy `descriptions.json` / `subjects.json` key is read
  first until migration, and descriptions are global to every world that places
  the picture.
  Legacy files are moved into the store by **migration**, and what nothing
  places any more is deleted by **collection**: both are maintenance runs
  started from Settings, Storage, Image store, never at startup, and neither is
  ever pointed at the frozen campaign's `home/`. Collection fails closed
  (a root it cannot parse, a link, a placement whose object has not arrived,
  anything younger than its grace period) and needs the token of a scan.
  Neither module appears in `store/locks/_domain.py`'s domain lists, because the guard
  surveys by `cid` and each takes a root; the migration's campaign writes take
  `campaign_lock` anyway.
  What the store promises is in `docs/reference/storage.md`; the image-store
  implementation's contracts are in `store/image_store.py`, `image_refs.py`,
  `store/image_migration/` and `store/image_gc/`.
