package it.trinka.seriea;

import android.app.Activity;
import android.content.Intent;
import android.graphics.Color;
import android.net.ConnectivityManager;
import android.net.NetworkCapabilities;
import android.net.Uri;
import android.os.Bundle;
import android.webkit.JavascriptInterface;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

/** Guscio WebView: interfaccia e dati vivono su GitHub Pages e si aggiornano senza reinstallare. */
public class MainActivity extends Activity {
    private WebView web;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        getWindow().setStatusBarColor(Color.parseColor("#0f1720"));
        getWindow().setNavigationBarColor(Color.parseColor("#17212c"));

        web = new WebView(this);
        web.setBackgroundColor(Color.parseColor("#0f1720"));
        setContentView(web);

        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);   // cache offline dei pronostici (localStorage)
        // online: sempre la versione più recente; offline: l'ultima salvata
        s.setCacheMode(isOnline() ? WebSettings.LOAD_NO_CACHE : WebSettings.LOAD_CACHE_ELSE_NETWORK);

        web.addJavascriptInterface(new Bridge(), "Android");
        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest req) {
                Uri u = req.getUrl();
                if (u.toString().startsWith(BuildConfig.APP_URL)) return false;
                startActivity(new Intent(Intent.ACTION_VIEW, u));   // notizie, download APK -> browser
                return true;
            }
        });

        if (savedInstanceState != null) web.restoreState(savedInstanceState);
        else loadFresh();
    }

    private long pausedAt = 0;

    private void loadFresh() {
        String url = BuildConfig.APP_URL;
        if (isOnline()) url += "?t=" + System.currentTimeMillis();   // salta la cache di GitHub Pages
        web.loadUrl(url);
    }

    @Override
    protected void onPause() {
        super.onPause();
        pausedAt = System.currentTimeMillis();
    }

    @Override
    protected void onResume() {
        super.onResume();
        // se l'app è rimasta in background più di 15 minuti, ricarica tutto
        if (pausedAt > 0 && System.currentTimeMillis() - pausedAt > 15 * 60 * 1000 && isOnline()) {
            web.getSettings().setCacheMode(WebSettings.LOAD_NO_CACHE);
            loadFresh();
        }
    }

    private boolean isOnline() {
        ConnectivityManager cm = getSystemService(ConnectivityManager.class);
        if (cm == null || cm.getActiveNetwork() == null) return false;
        NetworkCapabilities nc = cm.getNetworkCapabilities(cm.getActiveNetwork());
        return nc != null && nc.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET);
    }

    @Override
    protected void onSaveInstanceState(Bundle out) {
        super.onSaveInstanceState(out);
        web.saveState(out);
    }

    @Override
    public void onBackPressed() {
        if (web.canGoBack()) web.goBack();
        else super.onBackPressed();
    }

    class Bridge {
        @JavascriptInterface
        public int versionCode() {
            return BuildConfig.VERSION_CODE;
        }
    }
}
