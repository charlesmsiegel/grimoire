import { taskFromHash, taskHash } from "./taskHash";

test("a task key round-trips through the hash, whatever it holds", () => {
  for (const key of ["summary", "scene break", "odd#key", "100%"]) {
    expect(taskFromHash(taskHash(key))).toBe(key);
  }
});

test("a malformed escape is no task, never an exception", () => {
  expect(taskFromHash("#task-%")).toBeNull();
  expect(taskFromHash("#task-%ZZ")).toBeNull();
});

test("a hash that names no task is no task", () => {
  expect(taskFromHash("")).toBeNull();
  expect(taskFromHash("#rates")).toBeNull();
  expect(taskFromHash("#task-")).toBeNull();
});
