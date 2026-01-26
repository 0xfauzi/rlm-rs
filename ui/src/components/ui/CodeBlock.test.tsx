import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { CodeBlock } from "./CodeBlock";

describe("CodeBlock", () => {
  it("preserves indentation, blank lines, and tabs", () => {
    const sample = "\tdef greet(name):\n\t\tprint('hello')\n\n\t# done\n";
    const { container } = render(<CodeBlock content={sample} showLineNumbers={false} />);
    const codeElement = container.querySelector("code");
    expect(codeElement).not.toBeNull();
    expect((codeElement as HTMLElement).textContent).toBe(sample);
  });
});
