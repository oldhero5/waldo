import React from "react";

type DemoProps = {
  src: string;
  poster?: string;
  caption?: string;
  width?: string | number;
};

export default function Demo({ src, poster, caption, width = "100%" }: DemoProps) {
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
        src={src}
        poster={poster}
        controls
        muted
        loop
        playsInline
        preload="metadata"
        style={{ width, display: "block" }}
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
