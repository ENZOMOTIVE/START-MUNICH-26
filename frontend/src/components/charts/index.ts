/**
 * Hand-built SVG/HTML charts. All colours are CSS variables (dark mode flips them),
 * all charts fill their container's width, animate gently only when motion is
 * welcome, and carry a text alternative (aria-label, legend or hidden table).
 */
export { BarList, type BarListItem, type BarListProps } from "./BarList";
export { Donut, type DonutDatum, type DonutProps } from "./Donut";
export { Gauge, type GaugeBand, type GaugeProps } from "./Gauge";
export { Histogram, type HistogramBand, type HistogramMarker, type HistogramProps } from "./Histogram";
export { Meter, type MeterProps } from "./Meter";
export { Sparkline, type SparklineProps } from "./Sparkline";
export { StackedBars, type StackedBarsProps, type StackedBucket, type StackedSeries } from "./StackedBars";
export { StageFunnel, type FunnelStage, type StageFunnelProps } from "./StageFunnel";
export { SERIES, defaultFormat, seriesColor, useWidth } from "./shared";
