import { readFileSync, readdirSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const sourceRoot = resolve(import.meta.dirname)
const features = ['dashboard', 'test', 'leaderboard'] as const

function source(path: string): string {
  return readFileSync(resolve(sourceRoot, path), 'utf-8')
}

function featureSource(path: string): string {
  const root = resolve(sourceRoot, path)
  return readdirSync(root, { recursive: true, withFileTypes: true })
    .filter((entry) => entry.isFile() && /\.(ts|vue)$/.test(entry.name))
    .map((entry) => readFileSync(resolve(entry.parentPath, entry.name), 'utf-8'))
    .join('\n')
}

describe('frontend feature boundaries', () => {
  it('keeps the application shell free of feature API and state logic', () => {
    const app = source('App.vue')

    expect(app).not.toContain('/api/')
    for (const feature of features) {
      expect(app).not.toContain(`from './features/${feature}/api'`)
    }
    expect(app).not.toContain('fetchSummary')
    expect(app).not.toContain('submitDeepSweRun')
  })

  it('prevents dashboard, test, and leaderboard features from importing each other', () => {
    const sources = Object.fromEntries(
      features.map((feature) => [feature, featureSource(`features/${feature}`)]),
    ) as Record<(typeof features)[number], string>

    for (const feature of features) {
      for (const other of features) {
        if (feature !== other) {
          expect(sources[feature]).not.toContain(`features/${other}`)
        }
      }
    }
    expect(sources.test).not.toContain('fetchSummary')
    expect(sources.test).not.toContain('fetchRuns')
  })
})
