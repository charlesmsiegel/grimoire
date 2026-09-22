import { THUMB, thumbSet } from "./thumbs";

const url = (w: number) => `/img/seraphine?w=${w}`;

test("the fallback src is the bucket asked for, the tile by default", () => {
  expect(thumbSet(url, "120px").src).toBe("/img/seraphine?w=512");
  expect(thumbSet(url, "120px", THUMB.large).src).toBe("/img/seraphine?w=1024");
});

test("every bucket is a candidate, described by the width it guarantees a cover crop", () => {
  // The server fits a thumbnail in a bucket-by-bucket box, so a 2:3 portrait
  // comes back two thirds as wide as its bucket. Describing the 512 as "512w"
  // would let a browser fill a 512-device-pixel slot with a 341px-wide image.
  const set = thumbSet(url, "(max-width: 720px) 52px, 120px");
  expect(set.srcSet).toBe([
    "/img/seraphine?w=128 85w",
    "/img/seraphine?w=256 170w",
    "/img/seraphine?w=512 341w",
    "/img/seraphine?w=1024 682w",
  ].join(", "));
  expect(set.sizes).toBe("(max-width: 720px) 52px, 120px");
});

test("the buckets are the ones the server serves", () => {
  // Mirrors `THUMB_BUCKETS` in backend/src/grimoire/routes/common.py, which
  // test_thumbs.py holds to include each of these.
  expect(Object.values(THUMB)).toEqual([128, 256, 512, 1024]);
});
