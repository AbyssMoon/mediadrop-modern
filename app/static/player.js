(() => {
  "use strict";

  const isFiniteNumber = (value) => Number.isFinite(value);

  function seek(video, seconds) {
    const target = Math.max(0, video.currentTime + seconds);
    video.currentTime = isFiniteNumber(video.duration)
      ? Math.min(video.duration, target)
      : target;
  }

  function togglePlay(video) {
    if (video.paused || video.ended) {
      const promise = video.play();
      if (promise && typeof promise.catch === "function") {
        promise.catch(() => {});
      }
    } else {
      video.pause();
    }
  }

  function supportsStandardPip(video) {
    return Boolean(
      document.pictureInPictureEnabled &&
      typeof video.requestPictureInPicture === "function"
    );
  }

  function supportsWebkitPip(video) {
    return Boolean(
      typeof video.webkitSupportsPresentationMode === "function" &&
      video.webkitSupportsPresentationMode("picture-in-picture") &&
      typeof video.webkitSetPresentationMode === "function"
    );
  }

  function isPipActive(video) {
    if (document.pictureInPictureElement === video) return true;
    return video.webkitPresentationMode === "picture-in-picture";
  }

  async function togglePip(video) {
    if (supportsStandardPip(video)) {
      try {
        if (document.pictureInPictureElement === video) {
          await document.exitPictureInPicture();
        } else {
          if (document.pictureInPictureElement) {
            await document.exitPictureInPicture();
          }
          await video.requestPictureInPicture();
        }
      } catch (_) {
        // Browsers may reject PiP until the user has interacted with playback.
      }
      return;
    }

    if (supportsWebkitPip(video)) {
      video.webkitSetPresentationMode(
        isPipActive(video) ? "inline" : "picture-in-picture"
      );
    }
  }

  async function toggleFullscreen(video) {
    try {
      if (document.fullscreenElement) {
        await document.exitFullscreen();
      } else if (typeof video.requestFullscreen === "function") {
        await video.requestFullscreen();
      } else if (typeof video.webkitEnterFullscreen === "function") {
        video.webkitEnterFullscreen();
      }
    } catch (_) {
      // Fullscreen availability is browser- and gesture-dependent.
    }
  }

  function initPlayer(wrapper) {
    const video = wrapper.querySelector("[data-player-video]");
    if (!video) return;

    const speed = wrapper.querySelector("[data-player-speed]");
    const pipButton = wrapper.querySelector("[data-player-pip]");
    const wideButton = wrapper.querySelector("[data-player-wide]");
    const watchPage = wrapper.closest("[data-watch-page]");

    wrapper.querySelectorAll("[data-player-seek]").forEach((button) => {
      button.addEventListener("click", () => {
        seek(video, Number(button.dataset.playerSeek || 0));
      });
    });

    if (speed) {
      speed.addEventListener("change", () => {
        video.playbackRate = Number(speed.value) || 1;
      });
      video.addEventListener("ratechange", () => {
        const value = String(video.playbackRate);
        const matchingOption = Array.from(speed.options).find(
          (option) => Number(option.value) === video.playbackRate
        );
        if (matchingOption) speed.value = value;
      });
    }

    if (wideButton && watchPage) {
      const syncWideButton = () => {
        const active = watchPage.classList.contains("watch-page--wide");
        const label = active
          ? wideButton.dataset.exitLabel
          : wideButton.dataset.enterLabel;
        wideButton.setAttribute("aria-pressed", String(active));
        wideButton.setAttribute("aria-label", label);
        wideButton.title = label;
      };

      wideButton.addEventListener("click", () => {
        watchPage.classList.toggle("watch-page--wide");
        syncWideButton();
      });
      syncWideButton();
    } else if (wideButton) {
      wideButton.hidden = true;
    }

    if (pipButton && (supportsStandardPip(video) || supportsWebkitPip(video))) {
      pipButton.hidden = false;

      const syncPipButton = () => {
        const active = isPipActive(video);
        const label = active
          ? pipButton.dataset.exitLabel
          : pipButton.dataset.enterLabel;
        pipButton.setAttribute("aria-pressed", String(active));
        pipButton.setAttribute("aria-label", label);
        pipButton.title = label;
      };

      pipButton.addEventListener("click", () => togglePip(video));
      video.addEventListener("enterpictureinpicture", syncPipButton);
      video.addEventListener("leavepictureinpicture", syncPipButton);
      video.addEventListener("webkitpresentationmodechanged", syncPipButton);
      syncPipButton();
    }

    wrapper.addEventListener("keydown", (event) => {
      if (event.target !== wrapper && event.target !== video) return;

      // A focused native <video> already owns Space. Handling it here as well
      // causes a double toggle in some browsers (pause immediately followed by play).
      if (event.code === "Space" && event.target === video) return;

      switch (event.code) {
        case "Space":
        case "KeyK":
          if (event.repeat) return;
          event.preventDefault();
          togglePlay(video);
          break;
        case "ArrowLeft":
          event.preventDefault();
          seek(video, -10);
          break;
        case "ArrowRight":
          event.preventDefault();
          seek(video, 10);
          break;
        case "KeyM":
          if (event.repeat) return;
          event.preventDefault();
          video.muted = !video.muted;
          break;
        case "KeyF":
          if (event.repeat) return;
          event.preventDefault();
          toggleFullscreen(video);
          break;
        case "KeyP":
          if (event.repeat || !pipButton || pipButton.hidden) return;
          event.preventDefault();
          togglePip(video);
          break;
        default:
          break;
      }
    });
  }

  document.querySelectorAll("[data-enhanced-player]").forEach(initPlayer);

  document.addEventListener("pointerdown", (event) => {
    document.querySelectorAll("[data-player-help][open]").forEach((help) => {
      if (!help.contains(event.target)) help.removeAttribute("open");
    });
  });
})();
