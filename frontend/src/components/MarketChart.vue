<template>
  <div ref="el" class="chart"></div>
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts'

const props = defineProps({
  bars: { type: Array, default: () => [] },
  markers: { type: Array, default: () => [] },
  title: { type: String, default: '' },
})

const el = ref(null)
let chart = null

function nearestTradingDate(date) {
  const dates = props.bars.map((b) => b.trade_date)
  if (!dates.length) return null
  let best = dates[0]
  for (const d of dates) {
    if (d <= date) best = d
    else break
  }
  return best
}

function render() {
  if (!chart || !props.bars?.length) return

  const dates = props.bars.map((b) => b.trade_date)
  const kline = props.bars.map((b) => [b.open, b.close, b.low, b.high])

  const markPoints = (props.markers || [])
    .map((m) => {
      const coordDate = nearestTradingDate(m.date)
      if (!coordDate) return null
      const bar = props.bars.find((b) => b.trade_date === coordDate)
      if (!bar) return null
      return {
        name: m.label,
        coord: [coordDate, bar.high],
        value: m.label,
        label: {
          show: true,
          formatter: m.label,
          fontSize: 10,
          color: '#fff',
          backgroundColor: '#2f6bff',
          padding: [2, 4],
          borderRadius: 3,
          offset: [0, -6],
        },
        itemStyle: { color: '#2f6bff' },
        tooltip: {
          formatter: `${m.label} · ${m.date}<br/>${(m.detail || '').slice(0, 60)}`,
        },
      }
    })
    .filter(Boolean)

  chart.setOption(
    {
      title: { text: props.title, left: 0, top: 0, textStyle: { fontSize: 13, fontWeight: 600 } },
      tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } },
      grid: { left: 8, right: 12, top: 40, bottom: 8, containLabel: true },
      xAxis: {
        type: 'category',
        data: dates,
        axisLabel: { fontSize: 10 },
        boundaryGap: true,
      },
      yAxis: { scale: true, axisLabel: { fontSize: 10 } },
      series: [
        {
          type: 'candlestick',
          name: '日K',
          data: kline,
          itemStyle: {
            color: '#d03050',
            color0: '#1a9e5c',
            borderColor: '#d03050',
            borderColor0: '#1a9e5c',
          },
          markPoint: {
            symbol: 'pin',
            symbolSize: 26,
            symbolOffset: [0, '-60%'],
            data: markPoints,
            label: { show: false },
          },
        },
      ],
    },
    true
  )
}

function resize() {
  chart?.resize()
}

onMounted(() => {
  chart = echarts.init(el.value)
  render()
  window.addEventListener('resize', resize)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', resize)
  chart?.dispose()
})

watch(() => [props.bars, props.markers], render, { deep: true })
</script>

<style scoped>
.chart {
  width: 100%;
  height: 300px;
}
</style>
