"use client"

import { useEffect, useRef } from "react"
import {
  createChart,
  CandlestickSeries,
  IChartApi,
  ISeriesApi,
  CandlestickData,
} from "lightweight-charts"

type ChartProps = {
  data: CandlestickData[]
  height?: number
}

export default function PriceChart({ data, height = 400 }: ChartProps) {
  const chartContainerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const seriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null)

  useEffect(() => {
    if (!chartContainerRef.current) return

    const chart = createChart(chartContainerRef.current, {
      height,
      layout: {
        background: { color: "transparent" },
        textColor: "#d1d4dc",
      },
      grid: {
        vertLines: { color: "#1f2937" },
        horzLines: { color: "#1f2937" },
      },
      rightPriceScale: {
        borderColor: "#374151",
      },
      timeScale: {
        borderColor: "#374151",
      },
    })

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: "#22c55e",
      downColor: "#ef4444",
      borderVisible: false,
      wickUpColor: "#22c55e",
      wickDownColor: "#ef4444",
    })

    candleSeries.setData(data)

    chart.applyOptions({
      width: chartContainerRef.current.clientWidth,
    })

    const handleResize = () => {
      chart.applyOptions({
        width: chartContainerRef.current!.clientWidth,
      })
    }

    window.addEventListener("resize", handleResize)

    chartRef.current = chart
    seriesRef.current = candleSeries

    return () => {
      window.removeEventListener("resize", handleResize)
      chart.remove()
    }
  }, [])

  useEffect(() => {
    seriesRef.current?.setData(data)
  }, [data])

  return <div ref={chartContainerRef} className="w-full" />
}