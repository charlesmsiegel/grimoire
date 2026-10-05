import { quotedIn } from "./citation";

test("a quote is found in the stored text", () => {
  expect(quotedIn({ content: "She pressed a hand to her side." }, "hand to her side")).toBe(true);
});

test("a quote is found in the shown text when only a rule's output carries it", () => {
  // The server judges a citation against the prompt view, which a rule may
  // have changed; the client cannot see that view, but `shown` covers every
  // rule that applies to both.
  const m = { content: "She pressed a hand to her flank.", shown: "She pressed a hand to her side." };
  expect(quotedIn(m, "hand to her side")).toBe(true);
  expect(quotedIn(m, "hand to her flank")).toBe(true);
});

test("an empty needle matches nothing", () => {
  expect(quotedIn({ content: "anything" }, "")).toBe(false);
});

test("the match is case-blind on the post side; the needle arrives lower-cased", () => {
  expect(quotedIn({ content: "Raw", shown: "She NODDED." }, "she nodded")).toBe(true);
});
