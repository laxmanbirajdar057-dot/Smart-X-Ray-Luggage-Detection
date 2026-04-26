package com.laxman.xrayluggage.controller;

import org.springframework.stereotype.Controller;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.ResponseBody;
import org.springframework.core.io.ClassPathResource;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import java.nio.file.Files;

@Controller
public class PageController {

    @GetMapping(value = "/admin", produces = MediaType.TEXT_HTML_VALUE)
    @ResponseBody
    public ResponseEntity<byte[]> adminPage() throws Exception {
        var resource = new ClassPathResource("static/admin.html");
        byte[] bytes = Files.readAllBytes(resource.getFile().toPath());
        return ResponseEntity.ok()
                .contentType(MediaType.TEXT_HTML)
                .body(bytes);
    }
}
