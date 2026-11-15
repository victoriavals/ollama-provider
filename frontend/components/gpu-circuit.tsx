"use client";

import { useEffect, useRef } from "react";
import { Cpu } from "lucide-react";
import { usePreferences } from "./providers";

export default function GpuCircuit({ active }: { active: boolean }) {
  const root = useRef<HTMLDivElement>(null);
  const { motionEnabled } = usePreferences();
  useEffect(() => {
    if (!motionEnabled || !active) return;
    let disposed = false;
    let cleanup: (() => void) | undefined;
    void import("gsap").then(({ gsap }) => {
      if (disposed || !root.current) return;
      const context = gsap.context(() => {
        const animation = gsap.fromTo(
          ".circuit-packet",
          { x: -55, opacity: 0 },
          {
            x: 55,
            opacity: 1,
            duration: 0.8,
            stagger: 0.12,
            repeat: 1,
            ease: "power2.inOut",
          },
        );
        const onVisibility = () => {
          if (document.hidden) animation.pause();
          else animation.resume();
        };
        document.addEventListener("visibilitychange", onVisibility);
        cleanup = () =>
          document.removeEventListener("visibilitychange", onVisibility);
        onVisibility();
      }, root);
      const detach = cleanup;
      cleanup = () => {
        detach?.();
        context.revert();
      };
    });
    return () => {
      disposed = true;
      cleanup?.();
    };
  }, [motionEnabled, active]);
  return (
    <div ref={root} className="gpu-circuit" aria-hidden="true">
      <div className="circuit-orbit orbit-one" />
      <div className="circuit-orbit orbit-two" />
      <div className="circuit-line line-one">
        <i className="circuit-packet" />
      </div>
      <div className="circuit-line line-two">
        <i className="circuit-packet" />
      </div>
      <div className="circuit-line line-three">
        <i className="circuit-packet" />
      </div>
      <div className="circuit-chip">
        <Cpu strokeWidth={1.2} />
        <span>Inference engine</span>
      </div>
      <span className="circuit-node node-one" />
      <span className="circuit-node node-two" />
      <span className="circuit-node node-three" />
    </div>
  );
}
