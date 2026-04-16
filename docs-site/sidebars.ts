import type { SidebarsConfig } from "@docusaurus/plugin-content-docs";

const sidebars: SidebarsConfig = {
  main: [
    "intro",
    {
      type: "category",
      label: "Getting Started",
      collapsed: false,
      items: [
        "getting-started/installation",
        "getting-started/quickstart",
        "getting-started/configuration",
      ],
    },
    {
      type: "category",
      label: "UI Tour",
      collapsed: false,
      items: [
        "ui/overview",
        "ui/dashboard",
        "ui/datasets",
        "ui/upload",
        "ui/label",
        "ui/playground",
        "ui/train",
        "ui/workflows",
        "ui/deploy",
        "ui/agent",
      ],
    },
    {
      type: "category",
      label: "Architecture",
      items: [
        "architecture/overview",
        "architecture/data-model",
        "architecture/security",
      ],
    },
    {
      type: "category",
      label: "API Reference",
      items: [
        "api/overview",
        "api/auth",
        "api/upload",
        "api/label",
        "api/review",
        "api/train",
        "api/serve",
        "api/workflows",
        "api/admin",
      ],
    },
    {
      type: "category",
      label: "Workflow Blocks",
      items: [
        "workflows/overview",
        "workflows/detection",
        "workflows/specialized",
      ],
    },
    {
      type: "category",
      label: "Deployment",
      items: [
        "deployment/docker",
        "deployment/linux",
        "deployment/windows",
        "deployment/edge",
      ],
    },
    {
      type: "category",
      label: "Development",
      items: [
        "development/setup",
        "development/precommit",
        "development/testing",
        "development/contributing",
      ],
    },
  ],
};

export default sidebars;
