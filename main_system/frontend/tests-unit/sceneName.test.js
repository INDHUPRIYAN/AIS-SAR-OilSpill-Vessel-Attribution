import { describe, expect, it } from "vitest";
import { parseS1Name, sceneFact } from "../src/lib/sceneName";

describe("reading a Sentinel-1 product name", () => {
  it("reads mission, beam mode, product and polarisation from the ESA name", () => {
    expect(parseS1Name("S1A_IW_GRDH_1SDV_20230108T001008_20230108T001033_046685_059887_E5A1_COG")).toEqual({
      mission: "Sentinel-1A", mode: "IW", product: "GRD", resolution: "high",
      polarisation: "VV + VH (dual)", source: "ESA product name",
    });
    expect(parseS1Name("S1B_EW_GRDM_1SDH_20200101T000000_x").mode).toBe("EW");
  });

  it("returns nothing for a name that does not follow the convention", () => {
    for (const bad of [null, "", "sar_chip_no_metadata", "S1A_TEST", "S2A_MSIL1C_20230101"]) {
      expect(parseS1Name(bad)).toBeNull();
    }
  });
});

describe("a scene fact is recorded, derived, or said to be missing — never a constant", () => {
  it("prefers what the run recorded", () => {
    expect(sceneFact({ polarisation: "HH", scene_id: "S1A_IW_GRDH_1SDV_x" }, "polarisation")).toBe("HH");
  });
  it("falls back to the product name", () => {
    expect(sceneFact({ scene_id: "S1A_IW_GRDH_1SDV_x" }, "product")).toBe("GRD");
    expect(sceneFact({ scene_id: "S1A_IW_GRDH_1SDV_x" }, "mode")).toBe("IW");
  });
  it("says 'not recorded' for an uploaded chip with no name and no record", () => {
    for (const k of ["mission", "mode", "product", "polarisation"]) {
      expect(sceneFact({ scene_id: "sar_chip_no_metadata" }, k)).toBe("not recorded");
    }
  });
});
