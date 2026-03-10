"use client"

import { useEffect, useState } from "react"
import PriceChart from "@/components/ui/chart"
import { API_PREFIX } from "@/lib/api_prefix"
import { paths } from "@/generated/api"
import { CandlestickData } from "lightweight-charts"

export default function Dashboard() {
  const [candleData, setCandleData] = useState<CandlestickData[]>([])

  const symbol = "BAC"

  useEffect(() => {
    async function fetchData() {
      try {
        const res = await fetch(
          `${API_PREFIX}/livestream/pricedata?symbol=${symbol}`
        )

        const json = await res.json()

        const formatted: CandlestickData[] = json.map((c: any) => ({
          time: Math.floor(
            new Date(`${c.Date}T${c.Time}`).getTime() / 1000
          ),
          open: Number(c.Open),
          high: Number(c.High),
          low: Number(c.Low),
          close: Number(c.Close),
        }))

        setCandleData(formatted)
      } catch (err) {
        console.error("Failed to fetch price data", err)
      }
    }

    fetchData()
  }, [])

  return (
    <div className="p-10">
      <h2 className="text-xl font-bold mb-4">
        Price Chart
      </h2>

      <PriceChart data={candleData} height={350} />
    </div>
  )
}