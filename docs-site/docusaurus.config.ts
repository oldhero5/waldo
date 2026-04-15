import type { Config } from "@docusaurus/types";
import type * as Preset from "@docusaurus/preset-classic";
import { themes as prismThemes } from "prism-react-renderer";

const config: Config = {
  title: "Waldo",
  tagline: "SAM 3 video labeling + YOLO26 training pipeline",
  favicon: "img/favicon.svg",

  url: "https://oldhero5.github.io",
  baseUrl: "/waldo/",
  trailingSlash: false,

  organizationName: "oldhero5",
  projectName: "waldo",
  deploymentBranch: "gh-pages",

  onBrokenLinks: "warn",
  onBrokenAnchors: "warn",
  markdown: {
    hooks: {
      onBrokenMarkdownLinks: "warn",
    },
  },

  i18n: {
    defaultLocale: "en",
    locales: ["en"],
  },

  presets: [
    [
      "classic",
      {
        docs: {
          sidebarPath: "./sidebars.ts",
          routeBasePath: "/",
          editUrl: "https://github.com/oldhero5/waldo/tree/main/docs-site/",
        },
        blog: false,
        theme: {
          customCss: "./src/css/custom.css",
        },
      } satisfies Preset.Options,
    ],
  ],

  themeConfig: {
    image: "img/social-card.png",
    navbar: {
      title: "Waldo",
      logo: { alt: "Waldo", src: "img/favicon.svg" },
      items: [
        { type: "docSidebar", sidebarId: "main", position: "left", label: "Docs" },
        { to: "/getting-started/quickstart", label: "Quickstart", position: "left" },
        { to: "/api/overview", label: "API", position: "left" },
        { to: "/ui/overview", label: "UI Tour", position: "left" },
        {
          href: "https://github.com/oldhero5/waldo",
          label: "GitHub",
          position: "right",
        },
      ],
    },
    footer: {
      style: "dark",
      links: [
        {
          title: "Docs",
          items: [
            { label: "Quickstart", to: "/getting-started/quickstart" },
            { label: "Architecture", to: "/architecture/overview" },
            { label: "API Reference", to: "/api/overview" },
            { label: "UI Tour", to: "/ui/overview" },
          ],
        },
        {
          title: "Run",
          items: [
            { label: "Docker", to: "/deployment/docker" },
            { label: "Linux", to: "/deployment/linux" },
            { label: "Windows", to: "/deployment/windows" },
            { label: "Edge devices", to: "/deployment/edge" },
          ],
        },
        {
          title: "Contribute",
          items: [
            { label: "Dev setup", to: "/development/setup" },
            { label: "Pre-commit", to: "/development/precommit" },
            { label: "Testing", to: "/development/testing" },
            { label: "GitHub", href: "https://github.com/oldhero5/waldo" },
          ],
        },
      ],
      copyright: `© ${new Date().getFullYear()} Waldo · Self-hosted video labeling + training.`,
    },
    prism: {
      theme: prismThemes.github,
      darkTheme: prismThemes.dracula,
      additionalLanguages: ["bash", "python", "docker", "yaml", "json"],
    },
    colorMode: {
      defaultMode: "dark",
      respectPrefersColorScheme: true,
    },
  } satisfies Preset.ThemeConfig,
};

export default config;
