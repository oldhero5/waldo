import React from "react";
import useBaseUrl from "@docusaurus/useBaseUrl";

type DemoProps = {
  src: string;
  poster?: string;
  caption?: string;
  width?: string | number;
};

export default function Demo({ src, poster, caption, width = "100%" }: DemoProps) {
  // Resolve root-relative paths against the configured baseUrl
  // (Docusaurus only rewrites Markdown ![]() syntax, not raw <video src>).
  const resolvedSrc = useBaseUrl(src);
  const resolvedPoster = useBaseUrl(poster ?? "");

  return (
    <figure
      style={{
        margin: "1.5rem 0",
        border: "1px solid var(--ifm-color-emphasis-300)",
        borderRadius: 8,
        overflow: "hidden",
        background: "var(--ifm-background-surface-color)",
      }}
    >
      <video
        src={resolvedSrc}
        poster={poster ? resolvedPoster : undefined}
        controls
        muted
        loop
        playsInline
        preload="metadata"
        width={1152}
        height={656}
        style={{
          width,
          height: "auto",
          aspectRatio: "1152 / 656",
          display: "block",
          backgroundColor: "transparent",
          outline: "none",
        }}
      />
      {caption ? (
        <figcaption
          style={{
            padding: "0.6rem 0.9rem",
            fontSize: "0.85rem",
            color: "var(--ifm-color-emphasis-700)",
            fontFamily: "var(--ifm-font-family-monospace)",
          }}
        >
          {caption}
        </figcaption>
      ) : null}
    </figure>
  );
}
