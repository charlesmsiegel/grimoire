// A typed stand-in for an API value that a test only needs part of.
//
// A suite mocks `api.getScene` to answer with the three fields the page under
// test reads, not the forty the server sends. That used to be spelled
// `(api.getScene as any).mockResolvedValue({...})`, which checks nothing: a
// field renamed on the server, a string where the type now says a number, a
// union member that no longer exists -- each kept passing against a stub the
// real response could never be.
//
// `fixture` keeps the partiality and drops the blindness. Every field the
// literal DOES give is checked against the real type, all the way down; only
// the fields it leaves out go unchecked. The type to check against is never
// written at the call site -- it is inferred from where the value goes
// (`vi.mocked(api.getScene).mockResolvedValue(fixture({...}))` checks against
// what `getScene` resolves to), and `NoInfer` is what stops the literal from
// inferring a type of its own and checking against itself.
//
// Named values follow TypeScript's structural assignment rules: known fields
// are checked, but extra keys can survive. Check hoisted literals where they
// are defined with `satisfies DeepPartial<ActualApiType>`; passing a previously
// named value to `satisfies` cannot recover that literal's excess-key check.
//
// Reach for it only when a value is partial. A literal that is the whole type
// needs no wrapper, and is checked harder without one: a missing field is then
// a compile error rather than an `undefined` the code under test reads.

type UnionKeys<T> = T extends unknown ? keyof T : never;

/** `T` with every property optional, recursively; array elements are partial
 *  too, and functions are left as they are. Supplied tuples retain their slots
 *  and length; object fields within a slot can still be partial. A union stays
 *  a union, so a fixture still has to be one of its members. */
export type DeepPartial<T, Whole = T> =
  T extends (...args: never[]) => unknown ? T
  : T extends readonly unknown[] ? { [K in keyof T]: DeepPartial<T[K]> }
  : T extends object ? { [K in keyof T]?: DeepPartial<T[K]> }
    & { [K in Exclude<UnionKeys<Whole>, keyof T>]?: never }
  : T;

/** A partial `T`, checked against `T` field by field and handed on as one. */
export function fixture<T>(value: NoInfer<DeepPartial<T>>): T {
  return value as T;
}
