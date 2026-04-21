'use client';

import { useEffect, useRef } from 'react';
import {
  createChart,
  CandlestickSeries,
  LineSeries,
  HistogramSeries,
  createSeriesMarkers,
  IChartApi,
  ISeriesApi,
  ISeriesMarkersPluginApi,
  Time,
  IPriceLine,
  LineStyle,
  UTCTimestamp,
  CandlestickData,
  LineData,
  HistogramData,
} from 'lightweight-charts';

type Bar = {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume?: number | null;
  ema9?: number | null;
  vwap?: number | null;
};

export type SignalMarker = {
  bar_time: number;
  price: number;
};

type Props = {
  symbol: string | null;
  bars: Bar[];
  currentBar: Bar | null;
  last2High: number | null;
  stopLevel: number | null;
  signal?: SignalMarker | null;
};

export default function LiveAutoChart({
  symbol,
  bars,
  currentBar,
  last2High,
  stopLevel,
  signal,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const emaSeriesRef = useRef<ISeriesApi<'Line'> | null>(null);
  const vwapSeriesRef = useRef<ISeriesApi<'Line'> | null>(null);
  const volumeSeriesRef = useRef<ISeriesApi<'Histogram'> | null>(null);
  const markersRef = useRef<ISeriesMarkersPluginApi<Time> | null>(null);
  const highLineRef = useRef<IPriceLine | null>(null);
  const stopLineRef = useRef<IPriceLine | null>(null);
  const prevSymbolRef = useRef<string | null>(null);
  const shouldFitRef = useRef<boolean>(false);

  // Initialise the chart once
  useEffect(() => {
    if (!containerRef.current) return;

    const chart = createChart(containerRef.current, {
      height: 300,
      layout: { background: { color: 'transparent' }, textColor: '#1f2937' },
      grid: {
        vertLines: { color: '#e5e7eb' },
        horzLines: { color: '#e5e7eb' },
      },
      rightPriceScale: {
        borderColor: '#d1d5db',
        scaleMargins: { top: 0.1, bottom: 0.3 },
      },
      timeScale: {
        borderColor: '#d1d5db',
        timeVisible: true,
        secondsVisible: false,
      },
      crosshair: { mode: 0 },
    });

  const candleSeries = chart.addSeries(CandlestickSeries, {
    upColor: '#ffffff',        // hollow (white fill)
    downColor: '#ffffff',     // filled black
    borderVisible: true,       // REQUIRED for hollow look
    borderColor: '#000000',    // black outline
    wickUpColor: '#000000',
    wickDownColor: '#000000',
  });

    const emaSeries = chart.addSeries(LineSeries, {
      color: '#1e40af',
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: false,
      //title: 'EMA9',
    });

    const vwapSeries = chart.addSeries(LineSeries, {
      color: '#ef4444',           // violet-600
      lineWidth: 1,
      lineStyle: LineStyle.Solid,
      priceLineVisible: false,
      lastValueVisible: false,
      //title: 'VWAP',
    });

    // Volume histogram pinned to the lower 25% of the chart, its own scale.
    const volumeSeries = chart.addSeries(HistogramSeries, {
      priceFormat: { type: 'volume' },
      priceScaleId: 'volume',
      color: '#94a3b8',
      lastValueVisible: false,
      priceLineVisible: false,
    });
    chart.priceScale('volume').applyOptions({
      scaleMargins: { top: 0.75, bottom: 0 },
    });

    const markers = createSeriesMarkers(candleSeries, []);

    chart.applyOptions({ width: containerRef.current.clientWidth });

    const handleResize = () => {
      if (containerRef.current) {
        chart.applyOptions({ width: containerRef.current.clientWidth });
      }
    };
    window.addEventListener('resize', handleResize);

    chartRef.current = chart;
    candleSeriesRef.current = candleSeries;
    emaSeriesRef.current = emaSeries;
    vwapSeriesRef.current = vwapSeries;
    volumeSeriesRef.current = volumeSeries;
    markersRef.current = markers;

    return () => {
      window.removeEventListener('resize', handleResize);
      chart.remove();
      chartRef.current = null;
      candleSeriesRef.current = null;
      emaSeriesRef.current = null;
      vwapSeriesRef.current = null;
      volumeSeriesRef.current = null;
      markersRef.current = null;
      highLineRef.current = null;
      stopLineRef.current = null;
    };
  }, []);

  // Reset chart state (data + price lines + scales) whenever the active
  // symbol changes.  Without this, switching from e.g. a $320 stock to a
  // $30 stock leaves the chart zoomed to the old price range.
  useEffect(() => {
    const chart = chartRef.current;
    const candle = candleSeriesRef.current;
    const ema = emaSeriesRef.current;
    const vwap = vwapSeriesRef.current;
    const vol = volumeSeriesRef.current;
    const markers = markersRef.current;
    if (!chart || !candle || !ema || !vwap || !vol) {
      prevSymbolRef.current = symbol;
      return;
    }

    if (prevSymbolRef.current !== symbol) {
      candle.setData([]);
      ema.setData([]);
      vwap.setData([]);
      vol.setData([]);
      markers?.setMarkers([]);

      if (highLineRef.current) {
        try {
          candle.removePriceLine(highLineRef.current);
        } catch {
          /* ignore */
        }
        highLineRef.current = null;
      }
      if (stopLineRef.current) {
        try {
          candle.removePriceLine(stopLineRef.current);
        } catch {
          /* ignore */
        }
        stopLineRef.current = null;
      }

      try {
        chart.priceScale('right').applyOptions({ autoScale: true });
      } catch {
        /* ignore */
      }

      shouldFitRef.current = true;
      prevSymbolRef.current = symbol;
    }
  }, [symbol]);

  // Push the latest candle / EMA / VWAP / volume dataset on every update
  useEffect(() => {
    const chart = chartRef.current;
    const candle = candleSeriesRef.current;
    const ema = emaSeriesRef.current;
    const vwap = vwapSeriesRef.current;
    const vol = volumeSeriesRef.current;
    if (!chart || !candle || !ema || !vwap || !vol) return;

    const combined: Bar[] = [...bars];
    if (currentBar) {
      if (combined.length > 0 && combined[combined.length - 1].time === currentBar.time) {
        combined[combined.length - 1] = { ...combined[combined.length - 1], ...currentBar };
      } else {
        combined.push(currentBar);
      }
    }

    // Dedupe + sort ascending by time (lightweight-charts requires this).
    const deduped: Record<number, Bar> = {};
    for (const b of combined) deduped[b.time] = b;
    const sorted = Object.values(deduped).sort((a, b) => a.time - b.time);

    const candleData: CandlestickData[] = sorted.map((b) => ({
      time: Math.floor(b.time) as UTCTimestamp,
      open: b.open,
      high: b.high,
      low: b.low,
      close: b.close,
    }));
    candle.setData(candleData);

    const emaData: LineData[] = sorted
      .filter((b) => b.ema9 !== null && b.ema9 !== undefined && !Number.isNaN(b.ema9))
      .map((b) => ({
        time: Math.floor(b.time) as UTCTimestamp,
        value: b.ema9 as number,
      }));
    ema.setData(emaData);

    const vwapData: LineData[] = sorted
      .filter((b) => b.vwap !== null && b.vwap !== undefined && !Number.isNaN(b.vwap))
      .map((b) => ({
        time: Math.floor(b.time) as UTCTimestamp,
        value: b.vwap as number,
      }));
    vwap.setData(vwapData);

    const volumeData: HistogramData[] = sorted.map((b) => {
      const up = b.close >= b.open;
      return {
        time: Math.floor(b.time) as UTCTimestamp,
        value: Math.max(0, Number(b.volume ?? 0)),
        color: up ? 'rgba(34,197,94,0.55)' : 'rgba(239,68,68,0.55)',
      };
    });
    vol.setData(volumeData);

    // After a symbol switch, rescale time + price axes to the fresh dataset.
    if (shouldFitRef.current && candleData.length > 0) {
      try {
        chart.priceScale('right').applyOptions({ autoScale: true });
        chart.timeScale().fitContent();
      } catch {
        /* ignore */
      }
      shouldFitRef.current = false;
    }
  }, [bars, currentBar]);

  // Entry (last-2 high) line
  useEffect(() => {
    const series = candleSeriesRef.current;
    if (!series) return;

    if (highLineRef.current) {
      try {
        series.removePriceLine(highLineRef.current);
      } catch {
        /* ignore */
      }
      highLineRef.current = null;
    }

    if (last2High !== null && !Number.isNaN(last2High)) {
      highLineRef.current = series.createPriceLine({
        price: last2High,
        color: '#2563eb',
        lineStyle: LineStyle.Solid,
        lineWidth: 1,
        axisLabelVisible: false,
        title: 'Last-2 High (entry)',
      });
    }
  }, [last2High]);

  // Stop line (last-5 low − 0.06)
  useEffect(() => {
    const series = candleSeriesRef.current;
    if (!series) return;

    if (stopLineRef.current) {
      try {
        series.removePriceLine(stopLineRef.current);
      } catch {
        /* ignore */
      }
      stopLineRef.current = null;
    }

    if (stopLevel !== null && !Number.isNaN(stopLevel)) {
      stopLineRef.current = series.createPriceLine({
        price: stopLevel,
        color: '#dc2626',
        lineStyle: LineStyle.Solid,
        lineWidth: 1,
        axisLabelVisible: false,
        title: 'Stop (last-5 low − 0.06)',
      });
    }
  }, [stopLevel]);

  // Drop an up-arrow marker on the candle that fired the breakout signal
  useEffect(() => {
    const markers = markersRef.current;
    if (!markers) return;

    if (!signal) {
      markers.setMarkers([]);
      return;
    }

    markers.setMarkers([
      {
        time: Math.floor(signal.bar_time) as UTCTimestamp,
        position: 'belowBar',
        color: '#2563eb',
        shape: 'arrowUp',
        text: `Breakout @ ${signal.price.toFixed(2)}`,
      },
    ]);
  }, [signal]);

  return (
    <div
      ref={containerRef}
      className="w-full bg-white rounded-lg border border-gray-200 shadow-sm p-2"
    />
  );
}
