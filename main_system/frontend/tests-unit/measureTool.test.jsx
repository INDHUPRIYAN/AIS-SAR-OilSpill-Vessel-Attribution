/* The MAP-mode measure readout.
 *
 * The arithmetic is pinned in geodesy.test.js. What is pinned here is what a
 * reader is told: that the total is the legs added up, that the headline
 * bearing is start-to-end rather than a sum of headings, and that the method
 * travels with the number. A distance quoted in a report without its geodesy
 * cannot be reproduced by whoever reads the report.
 */

import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import MeasureTool from "../src/components/workspace/MeasureTool";
import { pathLengthKm } from "../src/lib/geodesy";

/* Two points in the Gulf, the basin the flagship scene covers. */
const A = [-92.0, 28.0];
const B = [-91.0, 28.5];
const C = [-90.5, 29.0];

describe("before there is anything to measure", () => {
  it("says how to start, rather than showing a zero", () => {
    render(<MeasureTool points={[]} />);
    expect(screen.getByTestId("measure-hint")).toHaveTextContent(/Click the map/);
    // 0.00 km would read as a measurement that came out at zero.
    expect(screen.queryByTestId("measure-total")).not.toBeInTheDocument();
  });

  it("reports one point as one point, with its position", () => {
    render(<MeasureTool points={[A]} />);
    expect(screen.getByTestId("measure-hint")).toHaveTextContent("28.0000°N");
    expect(screen.getByTestId("measure-hint")).toHaveTextContent("92.0000°W");
    expect(screen.queryByTestId("measure-total")).not.toBeInTheDocument();
  });
});

describe("a two-point measurement", () => {
  it("prints km, nm and bearing together", () => {
    render(<MeasureTool points={[A, B]} />);
    const total = screen.getByTestId("measure-total-value");
    expect(total).toHaveTextContent("112.63 km");
    expect(total).toHaveTextContent("60.81 nm");
    expect(total).toHaveTextContent("°");
  });

  it("states the geodesy beside the number", () => {
    render(<MeasureTool points={[A, B]} />);
    expect(screen.getByTestId("measure-method"))
      .toHaveTextContent(/great-circle \(haversine\)/);
  });
});

describe("a multi-leg path", () => {
  it("lists every leg", () => {
    render(<MeasureTool points={[A, B, C]} />);
    expect(within(screen.getByTestId("measure-legs")).getAllByRole("row"))
      .toHaveLength(3);                                   // header + two legs
    expect(screen.getByTestId("measure-leg-0")).toBeInTheDocument();
    expect(screen.getByTestId("measure-leg-1")).toBeInTheDocument();
  });

  it("shows a total that is the legs added up", () => {
    render(<MeasureTool points={[A, B, C]} />);
    const legs = [0, 1].map((i) => Number(
      within(screen.getByTestId(`measure-leg-${i}`)).getAllByRole("cell")[1]
        .textContent));
    const shown = Number(
      screen.getByTestId("measure-total-value").textContent.split(" km")[0]);
    expect(shown).toBeCloseTo(legs[0] + legs[1], 2);
    expect(shown).toBeCloseTo(pathLengthKm([A, B, C]), 2);
  });

  it("says the headline bearing is start-to-end", () => {
    // Without this line a reader could take the bearing for a course to steer
    // along the path, which it is not.
    render(<MeasureTool points={[A, B, C]} />);
    expect(screen.getByTestId("measure-bearing-note"))
      .toHaveTextContent(/start-to-end, not the sum of the legs/);
  });

  it("carries no such note for a single leg, where it would be noise", () => {
    render(<MeasureTool points={[A, B]} />);
    expect(screen.queryByTestId("measure-bearing-note")).not.toBeInTheDocument();
  });
});

describe("controls", () => {
  it("undoes the last point and clears the whole measurement", () => {
    const onUndo = vi.fn();
    const onClear = vi.fn();
    render(<MeasureTool points={[A, B]} onUndo={onUndo} onClear={onClear} />);
    fireEvent.click(screen.getByTestId("measure-undo"));
    fireEvent.click(screen.getByTestId("measure-clear"));
    expect(onUndo).toHaveBeenCalledTimes(1);
    expect(onClear).toHaveBeenCalledTimes(1);
  });

  it("disables both when there is nothing to undo or clear", () => {
    render(<MeasureTool points={[]} />);
    expect(screen.getByTestId("measure-undo")).toBeDisabled();
    expect(screen.getByTestId("measure-clear")).toBeDisabled();
  });
});
