import MapView from './components/MapView'

function App() {
  return (
    <main className="relative h-screen w-screen">
      <MapView />

      <div className="absolute left-6 top-6 z-10 rounded-2xl bg-white/90 px-5 py-4 shadow-xl backdrop-blur">
        <h1 className="text-xl font-bold text-slate-900">
          MAPGPT
        </h1>

        <p className="mt-1 text-sm text-slate-500">
          무엇을 지도에서 찾아볼까요?
        </p>
      </div>
    </main>
  )
}

export default App