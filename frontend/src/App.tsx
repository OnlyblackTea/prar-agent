import { PlanDocEditor } from './editor/PlanDocEditor'
import './App.css'

export default function App() {
  return (
    <div className="app">
      <header className="app-header">
        <h1>PRAR Agent</h1>
        <p className="subtitle">Plan / Review / Action / Review</p>
      </header>
      <main>
        <PlanDocEditor />
      </main>
    </div>
  )
}
