/**
 * FILE: features/cockpits/services/cockpitApi.ts
 * DESCRIPTION: CRUD sur /api/v1/cockpits (api-gateway, table Postgres
 *              `cockpits`) -- bibliothèque de compositions sauvegardées.
 */
import apiClient from '../../../services/apiClient'
import type { CockpitComposition } from '../types'

export interface CockpitRecord {
  id: string
  owner_username: string
  name: string
  description: string | null
  category: string | null
  tags: string[]
  composition: CockpitComposition
  created_at: string
  updated_at: string
}

export interface CockpitInput {
  name: string
  description?: string
  category?: string
  tags: string[]
  composition: CockpitComposition
}

function extractErrorMessage(error: unknown, fallback: string): string {
  const anyError = error as { response?: { data?: { detail?: string } }; message?: string }
  return anyError.response?.data?.detail || anyError.message || fallback
}

export async function listCockpits(): Promise<CockpitRecord[]> {
  try {
    const resp = await apiClient.get('/api/v1/cockpits')
    return resp.data as CockpitRecord[]
  } catch (error) {
    throw new Error(extractErrorMessage(error, 'Failed to list cockpits'))
  }
}

export async function createCockpit(body: CockpitInput): Promise<CockpitRecord> {
  try {
    const resp = await apiClient.post('/api/v1/cockpits', body)
    return resp.data as CockpitRecord
  } catch (error) {
    throw new Error(extractErrorMessage(error, 'Failed to create cockpit'))
  }
}

export async function updateCockpit(id: string, body: CockpitInput): Promise<CockpitRecord> {
  try {
    const resp = await apiClient.put(`/api/v1/cockpits/${id}`, body)
    return resp.data as CockpitRecord
  } catch (error) {
    throw new Error(extractErrorMessage(error, 'Failed to update cockpit'))
  }
}

export async function deleteCockpit(id: string): Promise<void> {
  try {
    await apiClient.delete(`/api/v1/cockpits/${id}`)
  } catch (error) {
    throw new Error(extractErrorMessage(error, 'Failed to delete cockpit'))
  }
}
