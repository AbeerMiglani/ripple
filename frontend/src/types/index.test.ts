import { describe, expect, it } from "vitest";
import { comparablePopulation, marginalFailures } from "./index";

describe("comparablePopulation", () => {
  it("prefers the uncapped raw figure when present", () => {
    expect(
      comparablePopulation({ population_affected_estimate: 65000, raw_population_affected: 120000 })
    ).toBe(120000);
  });

  it("falls back to the capped estimate for records with no raw figure", () => {
    expect(
      comparablePopulation({ population_affected_estimate: 4200, raw_population_affected: null })
    ).toBe(4200);
    expect(comparablePopulation({ population_affected_estimate: 4200 })).toBe(4200);
  });

  it("treats a genuine raw value of 0 as present, not missing", () => {
    // `??` only falls through on null/undefined, so a real zero must not be
    // swallowed into the capped estimate.
    expect(comparablePopulation({ population_affected_estimate: 500, raw_population_affected: 0 })).toBe(0);
  });
});

describe("marginalFailures", () => {
  it("prefers the explicit marginal field when present", () => {
    expect(
      marginalFailures({ wave: 1, failed_node_ids: ["a", "b"], marginal_failed_node_ids: ["b"] })
    ).toEqual(["b"]);
  });

  it("falls back to failed_node_ids for legacy records with no marginal field", () => {
    expect(marginalFailures({ wave: 0, failed_node_ids: ["a", "b"] })).toEqual(["a", "b"]);
  });
});
