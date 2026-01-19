"use client";

import { useEffect, useRef } from "react";
import { useToast } from "../contexts/ToastContext";
import { SEED_STORAGE_KEY, seedDevKey } from "../lib/seed";

export function useAutoSeed() {
  const { showToast } = useToast();
  const hasRunRef = useRef(false);

  useEffect(() => {
    if (hasRunRef.current) {
      return;
    }
    hasRunRef.current = true;

    if (typeof window === "undefined") {
      return;
    }
    if (window.localStorage.getItem(SEED_STORAGE_KEY) === "true") {
      return;
    }

    let cancelled = false;

    const run = async () => {
      const ok = await seedDevKey();
      if (cancelled) {
        return;
      }
      if (ok) {
        window.localStorage.setItem(SEED_STORAGE_KEY, "true");
        showToast("Seeded API key in LocalStack", "success");
      } else {
        showToast("Failed to seed API key. Check LocalStack status.", "error");
      }
    };

    void run();

    return () => {
      cancelled = true;
    };
  }, [showToast]);
}
