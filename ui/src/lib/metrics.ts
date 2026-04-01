/** Humanize raw YOLO metric keys into readable labels. */

const METRIC_LABELS: Record<string, string> = {
  "metrics/precision(B)": "Precision",
  "metrics/recall(B)": "Recall",
  "metrics/mAP50(B)": "mAP@50",
  "metrics/mAP50-95(B)": "mAP@50-95",
  "metrics/precision(M)": "Mask Precision",
  "metrics/recall(M)": "Mask Recall",
  "metrics/mAP50(M)": "Mask mAP@50",
  "metrics/mAP50-95(M)": "Mask mAP@50-95",
  "val/box_loss": "Box Loss",
  "val/seg_loss": "Seg Loss",
  "val/cls_loss": "Class Loss",
  "val/dfl_loss": "DFL Loss",
  "val/sem_loss": "Sem Loss",
  "train/box_loss": "Train Box Loss",
  "train/seg_loss": "Train Seg Loss",
  "train/cls_loss": "Train Class Loss",
  "train/dfl_loss": "Train DFL Loss",
  "fitness": "Fitness",
};

export function humanizeMetricKey(key: string): string {
  if (METRIC_LABELS[key]) return METRIC_LABELS[key];
  // Fallback: strip prefixes and clean up
  return key
    .replace("metrics/", "")
    .replace("val/", "")
    .replace("train/", "")
    .replace("(B)", "")
    .replace("(M)", " (mask)")
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

export function formatMetricValue(key: string, value: number): string {
  if (key.includes("loss")) return value.toFixed(3);
  if (value <= 1 && !key.includes("loss")) return `${(value * 100).toFixed(1)}%`;
  return value.toFixed(4);
}

/** Pick the most important metrics for summary display. */
export function pickKeyMetrics(metrics: Record<string, number>): { label: string; value: string; key: string }[] {
  const priority = [
    "metrics/mAP50(B)",
    "metrics/precision(B)",
    "metrics/recall(B)",
    "metrics/mAP50-95(B)",
    "metrics/mAP50(M)",
    "metrics/mAP50-95(M)",
  ];
  const result: { label: string; value: string; key: string }[] = [];
  for (const key of priority) {
    if (metrics[key] != null) {
      result.push({
        label: humanizeMetricKey(key),
        value: formatMetricValue(key, metrics[key]),
        key,
      });
    }
    if (result.length >= 4) break;
  }
  return result;
}
