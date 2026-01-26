"use client";

import { PrismLight as SyntaxHighlighter } from "react-syntax-highlighter";
import { vscDarkPlus } from "react-syntax-highlighter/dist/esm/styles/prism";

import bash from "react-syntax-highlighter/dist/esm/languages/prism/bash";
import json from "react-syntax-highlighter/dist/esm/languages/prism/json";
import markdown from "react-syntax-highlighter/dist/esm/languages/prism/markdown";
import python from "react-syntax-highlighter/dist/esm/languages/prism/python";
import typescript from "react-syntax-highlighter/dist/esm/languages/prism/typescript";

SyntaxHighlighter.registerLanguage("bash", bash);
SyntaxHighlighter.registerLanguage("json", json);
SyntaxHighlighter.registerLanguage("markdown", markdown);
SyntaxHighlighter.registerLanguage("python", python);
SyntaxHighlighter.registerLanguage("typescript", typescript);

interface CodeBlockProps {
  content: string;
  language?: "python" | "json" | "bash" | "markdown" | "typescript" | "text";
  showLineNumbers?: boolean;
  wrapLongLines?: boolean;
  className?: string;
}

const DEFAULT_FONT =
  "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace";

export function CodeBlock({
  content,
  language = "text",
  showLineNumbers = true,
  wrapLongLines = false,
  className = "",
}: CodeBlockProps) {
  return (
    <div
      className={`overflow-x-auto rounded-xl border border-slate-200 bg-slate-950 ${className}`.trim()}
    >
      <SyntaxHighlighter
        language={language === "text" ? undefined : language}
        style={vscDarkPlus}
        showLineNumbers={showLineNumbers}
        wrapLongLines={wrapLongLines}
        customStyle={{
          margin: 0,
          background: "transparent",
          padding: "0.85rem 1rem",
          fontFamily: DEFAULT_FONT,
          fontSize: "0.875rem",
          lineHeight: 1.6,
        }}
        codeTagProps={{ style: { fontFamily: DEFAULT_FONT } }}
        lineNumberStyle={{
          minWidth: "2.5rem",
          paddingRight: "0.9rem",
          marginRight: "0.9rem",
          textAlign: "right",
          userSelect: "none",
          color: "rgba(148, 163, 184, 0.9)",
          borderRight: "1px solid rgba(51, 65, 85, 0.6)",
        }}
      >
        {content}
      </SyntaxHighlighter>
    </div>
  );
}
