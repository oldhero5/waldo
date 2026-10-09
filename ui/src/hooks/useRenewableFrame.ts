import { useCallback, useEffect, useMemo, useState } from "react";
import { getFrame } from "../api";
import { useAuth } from "../contexts/authState";

const inFlight = new Map<string, Promise<{ image_url: string }>>();

// Only pending requests are shared. A subscriber leaving cannot abort a request
// another image is still using, and settled capabilities never survive a session.
function requestForCurrentIdentity(frameId: string, identity: string): Promise<{ image_url: string }> {
  const key = JSON.stringify([identity, frameId]);
  const pending = inFlight.get(key);
  if (pending) return pending;
  const request = getFrame(frameId).finally(() => { inFlight.delete(key); });
  inFlight.set(key, request);
  return request;
}

export function useRenewableFrame(frameId: string, initialUrl: string) {
  const { user, token } = useAuth();
  const identity = JSON.stringify([token, user?.id, user?.workspace_id, frameId]);
  const episode = useMemo(() => ({ identity, active: false, revision: 0, attempted: false, pending: false }), [identity]);
  const [media, setMedia] = useState({ identity, initialUrl, url: initialUrl, loadKey: 0, loading: false, error: null as string | null });
  // Changing the supplied URL is a new image load, but does not replenish the
  // failure budget for this frame/session. Successful decoding alone does that.
  if (media.identity !== identity || media.initialUrl !== initialUrl) {
    setMedia({ identity, initialUrl, url: initialUrl, loadKey: 0, loading: false, error: null });
  }
  const url = media.identity === identity && media.initialUrl === initialUrl ? media.url : initialUrl;

  const loadKey = media.identity === identity ? media.loadKey : 0;
  const load = useMemo(() => ({ episode, url, loadKey, active: false }), [url, loadKey, episode]);
  useEffect(() => {
    load.active = true;
    return () => {
      load.active = false;
      episode.revision++;
      episode.pending = false;
    };
  }, [load, episode]);

  useEffect(() => {
    episode.active = true;
    return () => { episode.active = false; episode.revision++; };
  }, [episode]);

  const renew = useCallback(async () => {
    if (!episode.active || episode.pending) return;
    episode.attempted = true;
    episode.pending = true;
    const revision = ++episode.revision;
    setMedia((previous) => ({ ...previous, loading: true, error: null }));
    try {
      const frame = await requestForCurrentIdentity(frameId, identity);
      if (!episode.active || !load.active || episode.revision !== revision) return;
      setMedia((previous) => ({ ...previous, url: frame.image_url, loadKey: previous.loadKey + 1, loading: false }));
    } catch (error) {
      if (!episode.active || !load.active || episode.revision !== revision) return;
      setMedia((previous) => ({ ...previous, loading: false, error: error instanceof Error ? error.message : "Could not reload image." }));
    } finally {
      if (episode.revision === revision) episode.pending = false;
    }
  }, [episode, frameId, identity, load]);

  // These closures belong to the rendered URL. Superseded image callbacks
  // cannot mark a newer image as loaded or start another renewal.
  const onLoad = useCallback(() => {
    if (!episode.active || !load.active) return;
    episode.attempted = false;
    setMedia((previous) => previous.url === url ? { ...previous, loading: false, error: null } : previous);
  }, [episode, load, url]);
  const onError = useCallback(() => {
    if (!episode.active || !load.active || episode.pending) return;
    if (episode.attempted) {
      setMedia((previous) => previous.url === url ? { ...previous, loading: false, error: "Image unavailable. Try again." } : previous);
    } else {
      void renew();
    }
  }, [episode, load, renew, url]);
  return { url, loadKey, loading: media.identity === identity && media.loading, error: media.identity === identity ? media.error : null, onLoad, onError, retry: () => { void renew(); } };
}
