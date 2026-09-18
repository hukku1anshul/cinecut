package app.cinecut.mobile;

import android.annotation.SuppressLint;
import android.app.Activity;
import android.app.AlertDialog;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.graphics.Color;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.text.InputType;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowInsets;
import android.view.WindowManager;
import android.webkit.CookieManager;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.window.OnBackInvokedDispatcher;

import java.io.UnsupportedEncodingException;
import java.net.URLEncoder;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * CineCut on a phone. The shortening runs on the CineCut server (the PC); this app opens its web app and adds what a
 * phone needs: it is in the Share menu of YouTube and other apps and hands the shared link to the Create page, lets the
 * viewer pick a video of their own, and plays short versions full screen. It never saves a video: downloads are ignored,
 * and the window is marked secure, so screenshots and screen recordings of it come out black.
 */
public class MainActivity extends Activity {
    private static final String PREFS = "cinecut";
    private static final String KEY_SERVER = "server";
    private static final int PICK_FILE = 7;
    private static final Pattern LINK = Pattern.compile("https?://\\S+");

    private FrameLayout root;
    private WebView web;
    private View fullScreen;
    private WebChromeClient.CustomViewCallback fullScreenDone;
    private ValueCallback<Uri[]> filePick;
    private AlertDialog shown;
    private String pendingLink;

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        getWindow().setFlags(WindowManager.LayoutParams.FLAG_SECURE, WindowManager.LayoutParams.FLAG_SECURE);
        root = new FrameLayout(this);
        root.setBackgroundColor(Color.parseColor("#11151C"));
        web = new WebView(this);
        root.addView(web, fill());
        root.setOnApplyWindowInsetsListener((v, insets) -> {      // keep the page clear of the status and navigation bars
            if (Build.VERSION.SDK_INT >= 30) {
                android.graphics.Insets b = insets.getInsets(WindowInsets.Type.systemBars() | WindowInsets.Type.ime());
                v.setPadding(b.left, b.top, b.right, b.bottom);
            } else {
                v.setPadding(insets.getSystemWindowInsetLeft(), insets.getSystemWindowInsetTop(),
                        insets.getSystemWindowInsetRight(), insets.getSystemWindowInsetBottom());
            }
            return insets;
        });
        setContentView(root);
        setUpWebView();
        if (Build.VERSION.SDK_INT >= 33) {                       // Android 13+: the system's back gesture comes here
            getOnBackInvokedDispatcher().registerOnBackInvokedCallback(OnBackInvokedDispatcher.PRIORITY_DEFAULT, this::goBack);
        }
        pendingLink = sharedLink(getIntent());
        if (server().isEmpty()) {
            askServer(true);
        } else {
            open();
        }
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        String link = sharedLink(intent);
        if (link != null) {
            pendingLink = link;
            if (!server().isEmpty()) {
                open();
            }
        }
    }

    private static FrameLayout.LayoutParams fill() {
        return new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT);
    }

    private void setUpWebView() {
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setMediaPlaybackRequiresUserGesture(false);
        s.setAllowFileAccess(false);
        s.setUserAgentString("CineCutApp/1.0 (Android " + Build.VERSION.RELEASE + ")");   // skips ngrok's browser warning page
        CookieManager.getInstance().setAcceptCookie(true);
        web.setBackgroundColor(Color.parseColor("#11151C"));
        // No DownloadListener is set: a download request does nothing, so the app never saves a file.
        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest req) {
                Uri u = req.getUrl();
                String home = Uri.parse(server()).getHost();
                if (home != null && home.equalsIgnoreCase(u.getHost())) {
                    return false;                                 // CineCut's own pages stay in the app
                }
                try {
                    startActivity(new Intent(Intent.ACTION_VIEW, u));
                } catch (ActivityNotFoundException ignored) {
                    // nothing can open it
                }
                return true;
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest req, WebResourceError err) {
                if (req.isForMainFrame()) {
                    showOffline();
                }
            }
        });
        web.setWebChromeClient(new WebChromeClient() {
            @Override
            public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> callback, FileChooserParams params) {
                if (filePick != null) {
                    filePick.onReceiveValue(null);
                }
                filePick = callback;
                Intent pick = new Intent(Intent.ACTION_GET_CONTENT).addCategory(Intent.CATEGORY_OPENABLE).setType("video/*");
                try {
                    startActivityForResult(Intent.createChooser(pick, getString(R.string.pick_video)), PICK_FILE);
                } catch (ActivityNotFoundException e) {
                    filePick = null;
                    return false;
                }
                return true;
            }

            @Override
            public void onShowCustomView(View view, CustomViewCallback done) {   // a video going full screen
                fullScreen = view;
                fullScreenDone = done;
                root.addView(view, fill());
                web.setVisibility(View.GONE);
            }

            @Override
            public void onHideCustomView() {
                leaveFullScreen();
            }
        });
    }

    private void leaveFullScreen() {
        if (fullScreen == null) {
            return;
        }
        root.removeView(fullScreen);
        fullScreen = null;
        web.setVisibility(View.VISIBLE);
        if (fullScreenDone != null) {
            fullScreenDone.onCustomViewHidden();
            fullScreenDone = null;
        }
    }

    /** Opens the Create page, with the shared link filled in when there is one. */
    private void open() {
        String page = server() + "/app/#/create";
        if (pendingLink != null) {
            try {
                page += "/" + URLEncoder.encode(pendingLink, "UTF-8").replace("+", "%20");
            } catch (UnsupportedEncodingException ignored) {
                // UTF-8 is always there
            }
            pendingLink = null;
        }
        web.loadUrl(page);
    }

    /** The first link in text shared from another app ("Watch this: https://youtu.be/...?si=..."). */
    private static String sharedLink(Intent intent) {
        if (intent == null || !Intent.ACTION_SEND.equals(intent.getAction())) {
            return null;
        }
        String text = intent.getStringExtra(Intent.EXTRA_TEXT);
        if (text == null) {
            return null;
        }
        Matcher m = LINK.matcher(text);
        return m.find() ? m.group() : null;
    }

    private String server() {
        return getSharedPreferences(PREFS, MODE_PRIVATE).getString(KEY_SERVER, "").trim();
    }

    private void askServer(boolean firstTime) {
        EditText box = new EditText(this);
        box.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        box.setHint("http://192.168.1.10:8080");
        box.setText(server());
        FrameLayout pad = new FrameLayout(this);
        int dp = (int) getResources().getDisplayMetrics().density;
        pad.setPadding(24 * dp, 8 * dp, 24 * dp, 0);
        pad.addView(box);
        dismissShown();
        shown = new AlertDialog.Builder(this)
                .setTitle(R.string.server_title)
                .setMessage(R.string.server_help)
                .setView(pad)
                .setCancelable(!firstTime)
                .setPositiveButton(R.string.save, (d, w) -> {
                    String v = box.getText().toString().trim().replaceAll("/+$", "");
                    if (!v.matches("(?i)https?://.+")) {
                        v = "http://" + v;
                    }
                    getSharedPreferences(PREFS, MODE_PRIVATE).edit().putString(KEY_SERVER, v).apply();
                    open();
                })
                .show();
    }

    private void showOffline() {
        dismissShown();
        shown = new AlertDialog.Builder(this)
                .setTitle(R.string.offline_title)
                .setMessage(getString(R.string.offline_text, server()))
                .setPositiveButton(R.string.try_again, (d, w) -> web.reload())
                .setNeutralButton(R.string.change_address, (d, w) -> askServer(false))
                .show();
    }

    private void dismissShown() {
        if (shown != null && shown.isShowing()) {
            shown.dismiss();
        }
        shown = null;
    }

    @SuppressLint("GestureBackNavigation")                      // only Android 8 to 12 come here; 13+ use the callback in onCreate
    @Override
    public void onBackPressed() {
        goBack();
    }

    private void goBack() {
        if (fullScreen != null) {
            leaveFullScreen();
        } else if (web.canGoBack()) {
            web.goBack();
        } else {
            dismissShown();
            shown = new AlertDialog.Builder(this)
                    .setTitle(R.string.leave_title)
                    .setPositiveButton(R.string.close, (d, w) -> finish())
                    .setNeutralButton(R.string.change_address, (d, w) -> askServer(false))
                    .setNegativeButton(R.string.stay, null)
                    .show();
        }
    }

    @Override
    protected void onActivityResult(int request, int result, Intent data) {
        if (request == PICK_FILE && filePick != null) {
            filePick.onReceiveValue(WebChromeClient.FileChooserParams.parseResult(result, data));
            filePick = null;
            return;
        }
        super.onActivityResult(request, result, data);
    }

    @Override
    protected void onPause() {
        super.onPause();
        CookieManager.getInstance().flush();                     // keep the login if Android closes the app
    }

    @Override
    protected void onDestroy() {
        dismissShown();
        web.destroy();
        super.onDestroy();
    }
}
