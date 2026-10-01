import Hls from 'hls.js'
import { useEffect, useRef, useState } from 'react'

export interface EvidenceMarker { at_seconds: number; label: string; screenshot_url?: string }

export default function MediaPlayer({ manifestUrl, markers = [] }: {
  manifestUrl: string; markers?: EvidenceMarker[]
}) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const hlsRef = useRef<Hls | null>(null)
  const [levels, setLevels] = useState<{ index: number; height: number }[]>([])
  const [quality, setQuality] = useState('auto')
  const [speed, setSpeed] = useState('1')
  const [duration, setDuration] = useState(0)
  const [position, setPosition] = useState(0)
  const [stallSeconds, setStallSeconds] = useState(0)
  const [error, setError] = useState('')
  const [selectedScreenshot, setSelectedScreenshot] = useState<string | null>(null)

  useEffect(() => {
    const video = videoRef.current
    if (!video) return
    const startedAt = performance.now()
    let waitingAt: number | null = null
    const onWaiting = () => { waitingAt = performance.now() }
    const onPlaying = () => {
      if (waitingAt !== null) {
        setStallSeconds(current => current + (performance.now() - waitingAt!) / 1000)
        waitingAt = null
      }
    }
    const onLoaded = () => {
      video.dataset.startupMs = String(Math.round(performance.now() - startedAt))
    }
    video.addEventListener('waiting', onWaiting)
    video.addEventListener('playing', onPlaying)
    video.addEventListener('loadeddata', onLoaded)
    if (video.canPlayType('application/vnd.apple.mpegurl')) {
      video.src = manifestUrl
    } else if (Hls.isSupported()) {
      const hls = new Hls({ startLevel: 0, enableWorker: true })
      hlsRef.current = hls
      hls.on(Hls.Events.MANIFEST_PARSED, () => {
        setLevels(hls.levels.map((level, index) => ({ index, height: level.height })))
      })
      hls.on(Hls.Events.ERROR, (_, data) => {
        if (data.details === Hls.ErrorDetails.BUFFER_STALLED_ERROR) onWaiting()
        if (data.fatal) setError(`Playback failed: ${data.details}`)
      })
      hls.attachMedia(video)
      hls.loadSource(manifestUrl)
    } else {
      setError('HLS playback is unavailable in this browser.')
    }
    return () => {
      video.removeEventListener('waiting', onWaiting)
      video.removeEventListener('playing', onPlaying)
      video.removeEventListener('loadeddata', onLoaded)
      hlsRef.current?.destroy()
      hlsRef.current = null
      video.removeAttribute('src')
      video.load()
    }
  }, [manifestUrl])

  function chooseQuality(value: string) {
    setQuality(value)
    if (hlsRef.current) hlsRef.current.currentLevel = value === 'auto' ? -1 : Number(value)
  }

  return <div className="media-player">
    <video ref={videoRef} controls playsInline preload="metadata" aria-label="Browser evidence recording"
      onDurationChange={event => setDuration(event.currentTarget.duration || 0)}
      onTimeUpdate={event => setPosition(event.currentTarget.currentTime)} />
    {error && <p role="alert" className="media-error">{error}</p>}
    <div className="media-controls">
      <label>Quality <select value={quality} onChange={event => chooseQuality(event.target.value)}>
        <option value="auto">Automatic</option>
        {levels.map(level => <option key={level.index} value={level.index}>{level.height}p</option>)}
      </select></label>
      <label>Speed <select value={speed} onChange={event => {
        setSpeed(event.target.value)
        if (videoRef.current) videoRef.current.playbackRate = Number(event.target.value)
      }}><option value="0.5">0.5×</option><option value="1">1×</option><option value="1.5">1.5×</option><option value="2">2×</option></select></label>
      <span aria-live="polite">{Math.floor(position)} / {Math.floor(duration)} s · {stallSeconds.toFixed(1)} s stalled</span>
    </div>
    {markers.length > 0 && <div className="evidence-timeline"><h4>Evidence timeline</h4>{markers.map((marker, index) => <button key={`${marker.at_seconds}-${index}`} type="button" onClick={() => {
      if (videoRef.current) videoRef.current.currentTime = marker.at_seconds
      setSelectedScreenshot(marker.screenshot_url || null)
    }}><span>{marker.at_seconds.toFixed(1)} s</span>{marker.label}</button>)}</div>}
    {selectedScreenshot && <div className="screenshot-view"><h4>Screenshot</h4><img src={selectedScreenshot} alt="Evidence captured at selected timeline point" /></div>}
  </div>
}
